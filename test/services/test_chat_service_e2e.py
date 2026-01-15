import pytest
import json
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.services import chat_service
from src.schemas.chat import ChatRequest
from src.llm.deepseek_chat_model import get_deepseek_llm
from src.llm.gemini_chat_model import get_gemini_llm
from src.services.llm_service import LLMService
from src.agents.github_react_agent import GithubReactAgent
from src.agents.main_agent import MainAgent
from src.configs.db import DATABASE_URL, get_async_engine
from src.dao import conversation_dao
from src.schemas.conversation import ConversationCreateSchema

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio

@pytest.fixture
async def db_session():
    """Fixture to provide a real database session."""
    # Clear the cache to ensure we get a new engine bound to the current event loop
    # This fixes the "Event loop is closed" error when running multiple async tests
    get_async_engine.cache_clear()
    
    # Create a new engine bound to the current event loop
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
    
    # Create a new session factory with the new engine
    AsyncSessionFactory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            await session.close()
    
    # Dispose the engine after use
    await engine.dispose()

@pytest.fixture
async def new_conversation(db_session: AsyncSession):
    """Fixture to create a new conversation for each test."""
    conv_create = ConversationCreateSchema(user_id=1)
    conversation = await conversation_dao.create_conversation(db_session, conv=conv_create)
    return conversation

# @pytest.mark.skip(reason="Skipping E2E test that requires external network access and can be flaky.")
async def test_stream_chat_response_gemini_e2e(db_session: AsyncSession, new_conversation):
    """
    End-to-end test for the stream_chat_response function using the Gemini model.
    """
    model_name = "gemini"
    llm = get_gemini_llm()
    llm_service = LLMService(llm=llm)
    
    github_agent = GithubReactAgent(llm_service=llm_service)
    # Initialize agent (optional for general chat but good practice)
    await github_agent.initialize()
    main_agent = MainAgent(llm_service=llm_service, github_agent=github_agent)

    request = ChatRequest(
        conversation_id=new_conversation['id'],
        message=f"Hello, {model_name}!  tell me why sky is blue.",
        model=model_name
    )

    full_response = ""
    stream_generator = chat_service.stream_chat_response(request, main_agent, db_session)
    
    print(f"\n--- Streaming Response for {model_name.upper()} ---")
    try:
        async for chunk in stream_generator:
            if chunk.startswith("data: "):
                data_str = chunk[len("data: "):-2]
                if data_str == "[DONE]":
                    break
                
                try:
                    data = json.loads(data_str)
                    content = data["choices"][0]["delta"].get("content", "")
                    print(content, end="", flush=True)
                    full_response += content
                except json.JSONDecodeError:
                    print(f"\n[Warning] Non-JSON chunk received: {data_str}")
                    # For compatibility or debugging, maybe append raw? 
                    # But we expect JSON now.
    except Exception as e:
        pytest.fail(f"Streaming failed for model '{model_name}' with an exception: {e}")
    finally:
        print(f"\n-------------------------------------")

    assert full_response, f"The streamed response for model '{model_name}' should not be empty."
    assert len(full_response) > 5, f"The streamed response for model '{model_name}' should have a reasonable length."

async def test_stream_chat_response_deepseek_e2e(db_session: AsyncSession, new_conversation):
    """
    End-to-end test for the stream_chat_response function using the DeepSeek model.
    """
    model_name = "deepseek"
    llm = get_deepseek_llm()
    llm_service = LLMService(llm=llm)

    github_agent = GithubReactAgent(llm_service=llm_service)
    await github_agent.initialize()
    main_agent = MainAgent(llm_service=llm_service, github_agent=github_agent)

    request = ChatRequest(
        conversation_id=new_conversation['id'],
        message=f"Hello, {model_name}! In one sentence, tell me what you are.",
        model=model_name
    )

    full_response = ""
    stream_generator = chat_service.stream_chat_response(request, main_agent, db_session)
    
    print(f"\n--- Streaming Response for {model_name.upper()} ---")
    try:
        async for chunk in stream_generator:
            if chunk.startswith("data: "):
                data_str = chunk[len("data: "):-2]
                if data_str == "[DONE]":
                    break
                
                try:
                    data = json.loads(data_str)
                    content = data["choices"][0]["delta"].get("content", "")
                    print(content, end="", flush=True)
                    full_response += content
                except json.JSONDecodeError:
                    print(f"\n[Warning] Non-JSON chunk received: {data_str}")
    except Exception as e:
        pytest.fail(f"Streaming failed for model '{model_name}' with an exception: {e}")
    finally:
        print(f"\n-------------------------------------")

    assert full_response, f"The streamed response for model '{model_name}' should not be empty."
    assert len(full_response) > 5, f"The streamed response for model '{model_name}' should have a reasonable length."
