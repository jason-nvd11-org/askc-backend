import src.configs.config
from langchain_core.language_models import BaseChatModel
from loguru import logger
from typing import AsyncIterator
from langchain_core.messages import BaseMessageChunk, BaseMessage

class LLMService:
    def __init__(self, llm: BaseChatModel):
        """Initializes the LLMService with a specific language model.

        Args:
            llm (BaseChatModel): An instance of a LangChain chat model 
                                 (e.g., ChatGoogleGenerativeAI or ChatDeepseek).
        """
        logger.info("Initializing LLMService...")
        self.llm = llm
        logger.info("LLMService initialized.")



    async def ainvoke(self, prompt: str) -> BaseMessage:
        """Invokes the language model with a given prompt and returns the full response.

        Args:
            prompt (str): The input prompt to send to the language model.

        Returns:
            BaseMessage: The complete response object from the language model.
        """
        logger.info(f"LLMService ainvoking with prompt: {prompt}")
        response = await self.llm.ainvoke(prompt)
        logger.info("LLMService ainvocation complete.")
        return response



    def astream(self, prompt: str) -> AsyncIterator[BaseMessageChunk]:
        """Streams the response from the language model for a given prompt.

        This method returns an async iterator that yields chunks of the response
        as they become available.

        Args:
            prompt (str): The input prompt to send to the language model.

        Returns:
            AsyncIterator[BaseMessageChunk]: An async iterator yielding response chunks.
        """
        logger.info(f"LLMService astreaming with prompt: {prompt}")
        return self.llm.astream(prompt)
