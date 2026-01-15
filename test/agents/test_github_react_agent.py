import pytest
from src.services.llm_service import LLMService
from src.llm.gemini_chat_model import get_gemini_llm
from src.agents.github_react_agent import GithubReactAgent

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio

@pytest.fixture
async def github_react_agent() -> GithubReactAgent:
    gemini_model = get_gemini_llm()
    llm_service = LLMService(llm=gemini_model)
    agent = GithubReactAgent(llm_service=llm_service)
    # Ensure initialized (in a real app, this might be done at startup)
    await agent.initialize()
    return agent

async def test_github_react_agent_e2e(github_react_agent: GithubReactAgent):
    """
    E2E test for GithubReactAgent using ReAct prompting.
    """
    query = "List first 3 repositories for user nvd11"
    
    print(f"\n--- Github React Agent Response ---")
    full_response = ""
    try:
        async for chunk in github_react_agent.astream(query):
            content = chunk.content
            print(content, end="", flush=True)
            if isinstance(content, str):
                full_response += content
            elif isinstance(content, list):
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
    # Check if tool was actually called (by checking for System Executing message)
    # The agent yields: [System: Executing tool_name...]
    assert "[System: Executing" in full_response or "get_repo_list" in full_response
