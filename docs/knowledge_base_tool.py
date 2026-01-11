from typing import Optional, Type, Any
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from src.services.retrieval_service import RetrievalService

# 1. Define Input Schema
class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(description="The search query, e.g., 'CPU frequency', 'Memory interface'.")
    topic: Optional[str] = Field(default=None, description="Specific topic to search within, e.g., 'VisionFive 2'. If not provided, searches all documents.")

# 2. Define Tool Class
class SearchKnowledgeBaseTool(BaseTool):
    name: str = "search_knowledge_base"
    description: str = (
        "Search for information in the Knowledge Base. "
        "Use this tool when you need technical details, specifications, or descriptions "
        "about hardware or software documented in the knowledge base."
    )
    args_schema: Type[BaseModel] = SearchKnowledgeBaseInput
    
    # Private attribute to hold the service, excluded from Pydantic validation
    _retrieval_service: RetrievalService
    
    def __init__(self, session: AsyncSession, **kwargs):
        super().__init__(**kwargs)
        self._retrieval_service = RetrievalService(session)

    def _run(self, query: str, topic: Optional[str] = None) -> str:
        """Synchronous run - not implemented."""
        raise NotImplementedError("SearchKnowledgeBaseTool does not support synchronous execution.")

    async def _arun(self, query: str, topic: Optional[str] = None) -> str:
        """Asynchronous run."""
        return await self._retrieval_service.search_knowledge_base(query, topic=topic)

# Factory function to keep compatibility with existing code
def create_retrieval_tool(session: AsyncSession) -> BaseTool:
    """
    Creates an instance of SearchKnowledgeBaseTool.
    """
    return SearchKnowledgeBaseTool(session=session)
