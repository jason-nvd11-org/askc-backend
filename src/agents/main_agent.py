import json
import re
from typing import AsyncIterator, Any, List, Optional
from loguru import logger
from langchain_core.messages import BaseMessageChunk, SystemMessage, HumanMessage, AIMessageChunk, ToolMessage, BaseMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.services.llm_service import LLMService
from .base import BaseAgent
from .github_react_agent import GithubReactAgent

class DelegateInput(BaseModel):
    query: str = Field(description="The user's query to delegate.")

def create_delegate_tool() -> StructuredTool:
    async def delegate_func(query: str):
        return "Delegating..."
    
    return StructuredTool.from_function(
        func=None,
        coroutine=delegate_func,
        name="delegate_to_github",
        description="Delegate the user's query to the GitHub expert agent when the query is about GitHub repositories, issues, etc.",
        args_schema=DelegateInput
    )

class MainAgent(BaseAgent):
    def __init__(self, llm_service: LLMService, github_agent: GithubReactAgent):
        self.llm_service = llm_service
        self.github_agent = github_agent
        self.tools = [create_delegate_tool()]
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        tool_desc = []
        for tool in self.tools:
            tool_desc.append(f"Name: {tool.name}")
            tool_desc.append(f"Description: {tool.description}")
            tool_desc.append(f"Arguments: {json.dumps(tool.args, indent=2)}")
        
        tools_str = "\n".join(tool_desc)
        
        return f"""You are a helpful assistant and a router.
Your job is to answer general questions or delegate specialized tasks to expert agents.

You have access to the following tools:
{tools_str}

To use a tool, output a JSON blob wrapped in markdown code block like this:
```json
{{
  "action": "tool_name",
  "action_input": {{ "arg": "value" }}
}}
```

If you can answer directly, just reply.
"""

    def _parse_tool_call(self, text: str) -> dict | None:
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        return None

    async def _execute_tool(self, tool_name: str, tool_args: dict) -> AsyncIterator[BaseMessageChunk]:
        if tool_name == "delegate_to_github":
            query = tool_args.get("query")
            yield AIMessageChunk(content=f"\n\n[System: Handing off to GitHub Agent...]\n\n")
            # Stream from sub-agent
            async for chunk in self.github_agent.astream(query):
                yield chunk
        else:
            yield AIMessageChunk(content=f"Error: Unknown tool {tool_name}")

    async def ainvoke(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> Any:
        chunks = []
        async for chunk in self.astream(input, chat_history):
            chunks.append(chunk)
        return "".join([c.content for c in chunks if isinstance(c.content, str)])

    def astream(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        return self._astream_impl(input, chat_history)

    async def _astream_impl(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        messages: List[BaseMessage] = [SystemMessage(content=self.system_prompt)]
        if chat_history:
            messages.extend(chat_history)
        messages.append(HumanMessage(content=input))

        full_response = ""
        # 1. Think
        async for chunk in self.llm_service.llm.astream(messages):
            if chunk.content:
                yield chunk
                if isinstance(chunk.content, str):
                    full_response += chunk.content

        # 2. Parse & Act
        tool_call = self._parse_tool_call(full_response)
        if tool_call:
            # Delegate execution (streaming)
            async for chunk in self._execute_tool(tool_call["action"], tool_call.get("action_input", {})):
                yield chunk
