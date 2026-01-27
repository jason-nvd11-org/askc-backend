import os
import httpx
from typing import AsyncIterator, Any, List, Dict, Optional
from loguru import logger
from langchain_core.messages import BaseMessageChunk, SystemMessage, HumanMessage, AIMessage, ToolMessage, BaseMessage, AIMessageChunk
from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_tool_calling_agent, AgentExecutor
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession

from src.configs.config import yaml_configs
from src.services.llm_service import LLMService
from src.tools.mcp_tool_converter import McpToolConverter
from .base import BaseAgent

# This is the "Old Paradigm" Agent using AgentExecutor
# Note: This is for educational comparison and may have limitations in streaming control.

class GithubExecutorAgent(BaseAgent):
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.agent_executor: Optional[AgentExecutor] = None
        
        # Load config
        mcp_config = yaml_configs.get("mcp", {}).get("servers", {}).get("github", {})
        self.url = mcp_config.get("url")
        auth_config = mcp_config.get("auth", {})
        self.token = os.getenv(auth_config.get("token_env_var"), "")
        self.headers = {auth_config.get("header_name", "X-Github-Token"): self.token}

    async def initialize(self):
        """
        Connects to MCP, fetches tools, and builds the AgentExecutor.
        """
        if not self.url or not self.token:
            logger.error("GitHub Executor Agent configuration is missing.")
            return

        logger.info(f"Initializing GithubExecutorAgent by connecting to MCP at {self.url}...")
        
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
                async with streamable_http_client(url=self.url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        # 1. Handshake & Get Instructions
                        init_result = await session.initialize()
                        server_instructions = getattr(init_result, 'instructions', '')

                        # 2. Get Tools from MCP and convert them
                        mcp_tools = await session.list_tools()
                        langchain_tools = [McpToolConverter.convert(tool) for tool in mcp_tools.tools]
                        
                        # 3. Build the Prompt
                        # create_tool_calling_agent requires a prompt with specific placeholders
                        prompt = ChatPromptTemplate.from_messages([
                            ("system", f"You are a helpful GitHub expert.\n{server_instructions}"),
                            ("human", "{input}"),
                            ("placeholder", "{agent_scratchpad}"),
                        ])
                        
                        # 4. Create the Tool Calling Agent (the "brain")
                        agent = create_tool_calling_agent(self.llm_service.llm, langchain_tools, prompt)
                        
                        # 5. Create the Agent Executor (the "body"/controller)
                        self.agent_executor = AgentExecutor(
                            agent=agent, 
                            tools=langchain_tools, 
                            verbose=True # Set to True for detailed logs
                        )
                        logger.info("GithubExecutorAgent initialized successfully.")

        except Exception as e:
            logger.error(f"Error initializing GithubExecutorAgent: {e}")
            raise

    async def ainvoke(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> Any:
        if not self.agent_executor:
            await self.initialize()
        
        if self.agent_executor:
            # AgentExecutor expects a dictionary with 'input' and 'chat_history'
            result = await self.agent_executor.ainvoke({
                "input": input,
                "chat_history": chat_history or []
            })
            return result.get("output", "No output from agent.")
        return "Error: AgentExecutor not initialized."

    async def astream(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        if not self.agent_executor:
            await self.initialize()

        if self.agent_executor:
            # AgentExecutor's astream yields dictionaries, not BaseMessageChunks directly.
            # We need to adapt this to our BaseAgent interface.
            async for chunk in self.agent_executor.astream({
                "input": input,
                "chat_history": chat_history or []
            }):
                # The streamed chunk is a dictionary. We extract the relevant part.
                # This part is more complex to map back to pure AIMessageChunks
                # which is a primary reason we chose the manual loop for fine-grained control.
                if "output" in chunk:
                    yield AIMessageChunk(content=chunk["output"])
                elif "actions" in chunk:
                     for action in chunk["actions"]:
                         yield AIMessageChunk(content=f"[Thinking: Calling tool `{action.tool}` with args {action.tool_input}...]\n")
                elif "steps" in chunk:
                    # Final answer might be here
                    pass
        else:
            yield AIMessageChunk(content="Error: AgentExecutor not initialized.")
