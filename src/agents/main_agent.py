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
        description="**MANDATORY**: Use this tool for ALL GitHub-related queries (repos, issues, prs, etc). Delegate the task to the GitHub expert agent.",
        args_schema=DelegateInput
    )

class MainAgent(BaseAgent):
    def __init__(self, llm_service: LLMService, github_agent: GithubReactAgent):
        self.llm_service = llm_service
        self.github_agent = github_agent
        self.tools = [create_delegate_tool()]
        self.system_prompt = self._build_system_prompt()
        
        # Tool Mapping for scalability
        self.tool_mapping = {
            "delegate_to_github": {
                "agent": self.github_agent,
                "name": "GitHub Agent"
            }
        }

    def _build_system_prompt(self) -> str:
        tool_desc = []
        tool_names = []
        for tool in self.tools:
            tool_desc.append(f"Name: {tool.name}")
            tool_desc.append(f"Description: {tool.description}")
            tool_desc.append(f"Arguments: {json.dumps(tool.args, indent=2)}")
            tool_names.append(f"`{tool.name}`")
        
        tools_str = "\n".join(tool_desc)
        tool_names_str = ", ".join(tool_names)
        
        return f"""You are a helpful assistant and a router.
Your job is to answer general questions or delegate specialized tasks to expert agents.

You have access to the following tools:
{tools_str}

CRITICAL INSTRUCTIONS:
1. You MUST ONLY use the tools listed above: {tool_names_str}.
2. Do NOT invent or hallucinate new tools.
3. Select the most appropriate tool based on the user's request and the tool descriptions.
4. Pass the user's ORIGINAL request as the argument to the tool.

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
        mapping = self.tool_mapping.get(tool_name)
        if mapping:
            agent = mapping["agent"]
            agent_name = mapping["name"]
            
            # Assuming all delegation tools take 'query' as main argument for now
            # This logic might need refinement if tool signatures vary significantly
            query = str(tool_args.get("query", ""))
            
            if not query:
                 yield AIMessageChunk(content=f"Error: Missing query for tool {tool_name}")
                 return

            yield AIMessageChunk(content=f"\n\n[System: I will ask the {agent_name} to help with: '{query}']\n\n")
            
            # Stream from sub-agent
            async for chunk in agent.astream(query):
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
        is_collecting_json = False
        
        # 1. Think
        async for chunk in self.llm_service.llm.astream(messages):
            content = chunk.content
            if not isinstance(content, str):
                continue
                
            full_response += content
            
            # Simple heuristic: if we see the start of a code block, we might be starting JSON
            if "```json" in full_response and not is_collecting_json:
                is_collecting_json = True
            
            # If we are NOT collecting JSON, we yield the content to the user
            # If we ARE collecting JSON, we suppress output (it's internal)
            if not is_collecting_json:
                yield chunk

        # 2. Parse & Act
        tool_call = self._parse_tool_call(full_response)
        if tool_call:
            tool_name = tool_call["action"]
            tool_input = tool_call.get("action_input", {})
            
            # Delegate execution (streaming)
            # Friendly message is now handled inside _execute_tool
            async for chunk in self._execute_tool(tool_name, tool_input):
                yield chunk
