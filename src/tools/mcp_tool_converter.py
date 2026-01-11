from loguru import logger
from langchain_core.tools import StructuredTool
from pydantic import create_model, Field
from mcp.types import Tool as McpTool

class McpToolConverter:
    @staticmethod
    def convert(tool: McpTool) -> StructuredTool:
        """
        Convert an MCP Tool to a LangChain StructuredTool.
        Dynamically generates Pydantic args_schema from JSON Schema.
        """
        logger.info(f"Converting tool: {tool.name}, Description: {tool.description}")
        
        # Define dynamic function for tool execution (placeholder)
        async def _tool_func(**kwargs):
            pass 

        # Create dynamic Pydantic model for args_schema
        fields = {}
        input_schema = tool.inputSchema or {}
        if "properties" in input_schema:
            for name, prop in input_schema["properties"].items():
                p_type = str 
                if prop.get("type") == "integer":
                    p_type = int
                elif prop.get("type") == "boolean":
                    p_type = bool
                
                desc = prop.get("description", "")
                is_required = name in input_schema.get("required", [])
                
                if is_required:
                    fields[name] = (p_type, Field(description=desc))
                else:
                    fields[name] = (p_type | None, Field(default=None, description=desc))
        
        args_model = create_model(f"{tool.name}Schema", **fields)

        return StructuredTool.from_function(
            func=None,
            coroutine=_tool_func, 
            name=tool.name,
            description=tool.description or "",
            args_schema=args_model
        )
