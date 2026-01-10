import pytest
from langchain_core.messages import AIMessage

from src.services.llm_service import LLMService
from src.llm.gemini_chat_model import get_gemini_llm

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio

@pytest.fixture
async def gemini_llm_service() -> LLMService:
    """
    Fixture to provide a real LLMService instance initialized with the
    GeminiChatModel.
    """
    gemini_model = get_gemini_llm()
    return LLMService(llm=gemini_model)

# @pytest.mark.skip(reason="Skipping E2E test that requires external network access and can be flaky.")
async def test_gemini_ainvoke_e2e(gemini_llm_service: LLMService):
    """
    End-to-end test for the LLMService.ainvoke() method using the real Gemini model.
    This test makes a real call to the Google Gemini API.
    """
    # 1. Prepare the prompt
    prompt = "Hello, Gemini! In one sentence, tell me what you are."

    # 2. Call the ainvoke method
    try:
        response = await gemini_llm_service.ainvoke(prompt)
    except Exception as e:
        pytest.fail(f"LLMService.ainvoke() failed with an exception: {e}")

    # 3. Print the response for verification
    print(f"\n--- Gemini Response ---\n{response.content}\n-----------------------")

    # 4. Assertions
    assert response is not None, "Response should not be None."
    assert isinstance(response, AIMessage), "Response should be an instance of AIMessage."
    assert response.content, "Response content should not be empty."
    assert len(response.content) > 5, "Response content should have a reasonable length."
