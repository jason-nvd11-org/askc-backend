from abc import ABC, abstractmethod
from typing import AsyncIterator, Any, List, Optional
from langchain_core.messages import BaseMessageChunk, BaseMessage

class BaseAgent(ABC):
    """
    Abstract base class for all agents.
    Defines the standard interface for interaction.
    """

    @abstractmethod
    async def ainvoke(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> Any:
        """
        Asynchronously invoke the agent with a single input.
        """
        pass

    @abstractmethod
    def astream(self, input: str, chat_history: Optional[List[BaseMessage]] = None) -> AsyncIterator[BaseMessageChunk]:
        """
        Asynchronously stream the agent's response.
        """
        pass
