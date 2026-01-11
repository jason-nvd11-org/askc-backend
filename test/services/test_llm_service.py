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

async def test_gemini_astream_e2e(gemini_llm_service: LLMService):
    """
    End-to-end test for the LLMService.astream() method using the real Gemini model.
    """
    # 1. Prepare the prompt
    prompt = "Count from 1 to 1000. Just the numbers."

    # 2. Call the astream method
    full_response = ""
    chunk_count = 0
    
    print(f"\n--- Gemini Stream Response ---")
    try:
        async for chunk in gemini_llm_service.astream(prompt):
            chunk_count += 1
            content = chunk.content
            # Print each chunk as it arrives
            print(content, end="", flush=True)
            if isinstance(content, str):
                full_response += content
            
            # Assertions per chunk
            assert content is not None
            # chunk.type should be 'ai' if it's AIMessageChunk, but BaseMessageChunk type hinting might hide it
            # We can check if it has 'type' attribute
            if hasattr(chunk, 'type'):
                assert chunk.type == 'AIMessageChunk' or chunk.type == 'ai'

    except Exception as e:
        pytest.fail(f"LLMService.astream() failed with an exception: {e}")
    print(f"\n------------------------------")

    # 3. Final Assertions
    assert chunk_count > 0, "Should receive at least one chunk."
    assert full_response, "Full response should not be empty."
    assert "1" in full_response
    assert "5" in full_response
