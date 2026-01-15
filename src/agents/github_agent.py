import os
import httpx
from typing import AsyncIterator, Any, List, Dict, Optional
from loguru import logger
from langchain_core.messages import BaseMessageChunk, SystemMessage, HumanMessage, AIMessageChunk, ToolMessage, BaseMessage
from langchain_core.tools import Tool, StructuredTool
from pydantic import create_model, Field

from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, Tool as McpTool

from src.configs.config import yaml_configs
from src.services.llm_service import LLMService
from src.tools.mcp_tool_converter import McpToolConverter
from .base import BaseAgent

class GithubAgent(BaseAgent):
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.system_prompt = "You are a GitHub expert. Use the provided tools to answer queries about repositories, issues, and pull requests."
        
        # Load config
        mcp_config = yaml_configs.get("mcp", {}).get("servers", {}).get("github", {})
        self.url = mcp_config.get("url")
        auth_config = mcp_config.get("auth", {})
        self.token = os.getenv(auth_config.get("token_env_var"), "")
        self.headers = {auth_config.get("header_name", "X-Github-Token"): self.token}

    async def _fetch_mcp_tools(self, session: ClientSession) -> List[McpTool]:
        """Fetch raw tools from MCP Server."""
        logger.info("Fetching tools from MCP...")
        result = await session.list_tools()
        logger.info(f"Fetched {len(result.tools)} tools from MCP.")
        return result.tools

    def _convert_mcp_tools(self, mcp_tools: List[McpTool], session: ClientSession) -> List[StructuredTool]:
        """Convert MCP Tools to LangChain StructuredTools."""
        return [McpToolConverter.convert(tool) for tool in mcp_tools]

    def _extract_instructions(self, init_result: Any) -> str:
        """Extract server instructions from initialization result."""
        if hasattr(init_result, 'instructions') and init_result.instructions:
            server_instructions = f"\n\n[Server Instructions]\n{init_result.instructions}"
            logger.info(f"Loaded server instructions.")
            return server_instructions
        return ""

    async def _execute_tool_call(self, session: ClientSession, tool_call: Any) -> ToolMessage:
        """Execute a single tool call and return the result message."""
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_id = tool_call["id"]
        
        logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
        
        try:
            result = await session.call_tool(tool_name, arguments=tool_args)
            
            tool_output_text = ""
            for content in result.content:
                if content.type == "text":
                    tool_output_text += content.text
            
            logger.info(f"Tool result: {tool_output_text[:100]}...")
        except Exception as e:
            tool_output_text = f"Error executing tool: {str(e)}"
            logger.error(tool_output_text)

        return ToolMessage(content=tool_output_text, tool_call_id=tool_call_id)

    async def _agent_loop(self, llm_with_tools, session: ClientSession, messages: List) -> AsyncIterator[BaseMessageChunk]:
        """Core ReAct loop: Think -> Act -> Observe -> Think."""
        while True:
            final_chunk: AIMessageChunk | None = None
            
            # 1. Think (Stream LLM response)
            async for chunk in llm_with_tools.astream(messages):
                if not isinstance(chunk, AIMessageChunk):
                    continue
                
                if final_chunk is None:
                    final_chunk = chunk
                else:
                    final_chunk += chunk
                
                if chunk.content:
                    yield chunk
            
            if not final_chunk:
                break

            messages.append(final_chunk)

            # 2. Decide (Check for tool calls)
            if not getattr(final_chunk, 'tool_calls', None):
                break
            
            # 3. Act (Execute tools)
            logger.info(f"AI requested {len(final_chunk.tool_calls)} tool calls")
            for tool_call in final_chunk.tool_calls:
                # Provide feedback to user
                yield AIMessageChunk(content=f"\n\n[Thinking: Calling tool `{tool_call['name']}`...]\n\n")
                
                tool_msg = await self._execute_tool_call(session, tool_call)
                messages.append(tool_msg)

    async def _connect_and_execute(self, input_text: str) -> AsyncIterator[BaseMessageChunk]:
        """Main entry point: Connects to MCP and orchestrates the agent execution."""
        if not self.url or not self.token:
            yield AIMessageChunk(content="Error: GitHub Agent configuration is missing.")
            return

        logger.info(f"Connecting to GitHub MCP at {self.url}...")
        
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
                async with streamable_http_client(url=self.url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        # 1. Handshake
                        logger.info("Initializing MCP Session...")
                        init_result = await session.initialize()
                        logger.info("MCP Session initialized.")
                        
                        # 2. Get Instructions
                        server_instructions = self._extract_instructions(init_result)

                        # 3. Get Tools
                        mcp_tools = await self._fetch_mcp_tools(session)
                        langchain_tools = self._convert_mcp_tools(mcp_tools, session)

                        # 4. Prepare LLM
                        llm_with_tools = self.llm_service.llm.bind_tools(langchain_tools)
                        
                        # 5. Build Context
                        full_system_prompt = self.system_prompt + server_instructions
                        messages = [
                            SystemMessage(content=full_system_prompt),
                            HumanMessage(content=input_text)
                        ]
                        
                        # 6. Execute Loop
                        async for chunk in self._agent_loop(llm_with_tools, session, messages):
                            yield chunk

        except Exception as e:
            logger.error(f"Error in GithubAgent: {e}")
            yield AIMessageChunk(content=f"Error executing GitHub Agent: {str(e)}")

    async def ainvoke(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> Any:
        # For simplicity, just collect stream
        chunks = []
        async for chunk in self._connect_and_execute(input):
            chunks.append(chunk)
        return "".join([c.content for c in chunks])

    def astream(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        return self._connect_and_execute(input)
