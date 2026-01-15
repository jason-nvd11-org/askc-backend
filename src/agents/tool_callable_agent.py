import os
from typing import List, Any
import json
from loguru import logger
from mcp.types import Tool as McpTool
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession
import httpx
from src.configs.config import yaml_configs
from src.services.llm_service import LLMService
from .base import BaseAgent

class ToolCallableAgent(BaseAgent):
    """
    A base agent that can interact with an MCP server but uses ReAct prompting 
    instead of native tool binding. Suitable for models that don't support bind_tools.
    """
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        # Load config
        mcp_config = yaml_configs.get("mcp", {}).get("servers", {}).get("github", {})
        self.url = mcp_config.get("url")
        auth_config = mcp_config.get("auth", {})
        self.token = os.getenv(auth_config.get("token_env_var"), "")
        self.headers = {auth_config.get("header_name", "X-Github-Token"): self.token}
        self.tools: List[McpTool] = []
        self.initialized = False

    async def _fetch_mcp_tools(self, session: ClientSession) -> List[McpTool]:
        """Fetch raw tools from MCP Server and store them."""
        logger.info("Fetching tools from MCP...")
        result = await session.list_tools()
        self.tools = result.tools
        logger.info(f"Fetched {len(self.tools)} tools from MCP.")
        return self.tools

    async def initialize(self):
        """
        Initialize the agent by fetching tools and preparing the system prompt.
        This establishes a temporary connection to the MCP server.
        """
        logger.info("Initializing Agent: Fetching tools and instructions...")
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
                async with streamable_http_client(url=self.url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        init_result = await session.initialize()
                        
                        # 1. Fetch Tools
                        await self._fetch_mcp_tools(session)
                        
                        # 2. Extract Instructions
                        instructions = self._extract_instructions(init_result)
                        
                        # 3. Format Tools for Prompt
                        tool_defs = self._format_tool_definitions(self.tools)
                        
                        # 4. Construct System Prompt (to be stored in subclass or self)
                        # We store the components so the subclass can assemble them
                        self.instructions = instructions
                        self.tool_definitions = tool_defs
                        
                        self.initialized = True
                        logger.info("Agent initialization complete.")
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise

    def _extract_instructions(self, init_result: Any) -> str:
        """Extract server instructions from initialization result."""
        if hasattr(init_result, 'instructions') and init_result.instructions:
            server_instructions = f"\n\n[Server Instructions]\n{init_result.instructions}"
            logger.info(f"Loaded server instructions.")
            return server_instructions
        return ""

    def _format_tool_definitions(self, tools: List[McpTool]) -> str:
        """
        Formats MCP tools into a string description for ReAct prompting.
        """
        prompt_lines = ["You have access to the following tools:\n"]
        
        for tool in tools:
            schema = json.dumps(tool.inputSchema, indent=2)
            prompt_lines.append(f"Name: {tool.name}")
            prompt_lines.append(f"Description: {tool.description}")
            prompt_lines.append(f"Arguments Schema: {schema}")
            prompt_lines.append("-" * 20)
            
        prompt_lines.append("""
To use a tool, please output a JSON blob wrapped in markdown code block like this:

```json
{
  "action": "tool_name",
  "action_input": {
    "arg1": "value1",
    "arg2": "value2"
  }
}
```

If you don't need to use a tool, just reply directly.
""")
        return "\n".join(prompt_lines)
