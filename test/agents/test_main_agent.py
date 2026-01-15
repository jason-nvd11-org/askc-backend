import pytest
from src.services.llm_service import LLMService
from src.llm.gemini_chat_model import get_gemini_llm
from src.agents.github_react_agent import GithubReactAgent
from src.agents.main_agent import MainAgent

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio

@pytest.fixture
async def main_agent() -> MainAgent:
    gemini_model = get_gemini_llm()
    llm_service = LLMService(llm=gemini_model)
    
    # Initialize GithubReactAgent
    github_agent = GithubReactAgent(llm_service=llm_service)
    # We might need to initialize it? MainAgent calls github_agent.astream which calls ensure_initialized.
    # So explicit init here is optional but good for readiness.
    await github_agent.initialize()
    
    return MainAgent(llm_service=llm_service, github_agent=github_agent)

async def test_main_agent_general_chat(main_agent: MainAgent):
    """
    Test general chat capabilities (no handoff).
    """
    query = "Hello, who are you?"
    print(f"\n--- Main Agent General Chat ---")
    full_response = ""
    async for chunk in main_agent.astream(query):
        content = chunk.content
        print(content, end="", flush=True)
        if isinstance(content, str):
            full_response += content
            
    print(f"\n-----------------------------")
    assert full_response
    # Verify it did NOT hand off
    assert "[System: Handing off to GitHub Agent...]" not in full_response
    assert len(full_response) > 5

async def test_main_agent_github_handoff(main_agent: MainAgent):
    """
    Test handoff to GithubReactAgent.
    """
    query = "List first 3 repositories for user nvd11"
    
    print(f"\n--- Main Agent Github Handoff ---")
    full_response = ""
    handoff_occurred = False
    
    async for chunk in main_agent.astream(query):
        content = chunk.content
        print(content, end="", flush=True)
        
        if isinstance(content, str):
            full_response += content
            if "[System: Handing off to GitHub Agent...]" in content:
                handoff_occurred = True
            
    print(f"\n-----------------------------")

    assert full_response
    assert handoff_occurred, "Should have detected handoff message"
    assert "mail-service" in full_response or "envoy-config" in full_response
