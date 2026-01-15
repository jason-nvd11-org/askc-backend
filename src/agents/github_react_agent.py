import json
import re
from typing import AsyncIterator, Any, List
from loguru import logger
from typing import Optional
from langchain_core.messages import BaseMessageChunk, SystemMessage, HumanMessage, AIMessageChunk, ToolMessage, BaseMessage
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx

from src.services.llm_service import LLMService
from .tool_callable_agent import ToolCallableAgent

class GithubReactAgent(ToolCallableAgent):
    def __init__(self, llm_service: LLMService):
        super().__init__(llm_service)
        self.base_system_prompt = "You are a GitHub expert. Use the provided tools to answer queries about repositories, issues, and pull requests."
        self.initialized = False

    async def ensure_initialized(self):
        if not self.initialized:
            await self.initialize()
            self.initialized = True

    def _parse_tool_call(self, text: str) -> dict | None:
        """Extracts JSON tool call from text."""
        # Try to find JSON block in markdown
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON object
            json_match = re.search(r"(\{.*\})", text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                return None
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    async def _execute_tool_ephemeral(self, tool_name: str, tool_args: dict) -> str:
        """
        Connects to MCP, executes a single tool, and disconnects.
        """
        logger.info(f"Connecting to MCP for tool execution: {tool_name}")
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
                async with streamable_http_client(url=self.url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments=tool_args)
                        
                        output_text = ""
                        for content in result.content:
                            if content.type == "text":
                                output_text += content.text
                        return output_text
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return f"Error executing tool: {str(e)}"

    async def _agent_loop(self, messages: List) -> AsyncIterator[BaseMessageChunk]:
        """ReAct Loop: Think -> Parse -> Act -> Observe -> Think"""
        MAX_TURNS = 5
        turn = 0
        
        while turn < MAX_TURNS:
            turn += 1
            full_response = ""
            
            # 1. Think
            async for chunk in self.llm_service.llm.astream(messages):
                if chunk.content:
                    yield chunk
                    if isinstance(chunk.content, str):
                        full_response += chunk.content
                    elif isinstance(chunk.content, list):
                         for part in chunk.content:
                             if isinstance(part, str): full_response += part
                             elif isinstance(part, dict) and "text" in part: full_response += part["text"]

            # 2. Parse
            tool_call_data = self._parse_tool_call(full_response)
            if not tool_call_data:
                break
            
            # 3. Act
            tool_name = tool_call_data.get("action")
            tool_args = tool_call_data.get("action_input", {})
            
            if not tool_name:
                 break

            messages.append(AIMessageChunk(content=full_response))
            yield AIMessageChunk(content=f"\n\n[Thinking: Decided to call tool `{tool_name}` with args {tool_args}...]\n\n")
            
            tool_result = await self._execute_tool_ephemeral(tool_name, tool_args)
            
            # 4. Observe
            messages.append(HumanMessage(content=f"Tool Output: {tool_result}"))

    def astream(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        return self._astream_impl(input, chat_history)

    async def _astream_impl(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        await self.ensure_initialized()
        
        full_system_prompt = f"{self.base_system_prompt}\n{getattr(self, 'instructions', '')}\n\n{getattr(self, 'tool_definitions', '')}"
        
        messages: List[BaseMessage] = [SystemMessage(content=full_system_prompt)]
        
        if chat_history:
            messages.extend(chat_history)
            
        messages.append(HumanMessage(content=input))
        
        async for chunk in self._agent_loop(messages):
            yield chunk
            
    async def ainvoke(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> Any:
        chunks = []
        async for chunk in self.astream(input, chat_history):
            chunks.append(chunk)
        return "".join([c.content for c in chunks if isinstance(c.content, str)])
