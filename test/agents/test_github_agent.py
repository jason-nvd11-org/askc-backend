import pytest
from src.services.llm_service import LLMService
from src.llm.gemini_chat_model import get_gemini_llm
from src.agents.github_agent import GithubAgent

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio

@pytest.fixture
async def github_agent() -> GithubAgent:
    gemini_model = get_gemini_llm()
    llm_service = LLMService(llm=gemini_model)
    return GithubAgent(llm_service=llm_service)

async def test_github_agent_e2e(github_agent: GithubAgent):
    """
    E2E test for GithubAgent.
    Should connect to real MCP server and list repos.
    """
    query = "List first 3 repositories for user nvd11"
    
    print(f"\n--- Github Agent Response ---")
    full_response = ""
    try:
        async for chunk in github_agent.astream(query):
            content = chunk.content
            print(content, end="", flush=True)
            if isinstance(content, str):
                full_response += content
            elif isinstance(content, list):
                # Handle list of parts (common in Gemini)
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        full_response += part["text"]
                    elif isinstance(part, str):
                        full_response += part
    except Exception as e:
        pytest.fail(f"Agent execution failed: {e}")
    print(f"\n-----------------------------")

    assert full_response
    assert len(full_response) > 10
