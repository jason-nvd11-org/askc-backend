import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from src.schemas.chat import ChatRequest
from src.schemas.message import MessageCreateSchema
from src.services.chat_service import stream_chat_response
from src.agents.base import BaseAgent

# Mock Agent to capture chat_history
class MockAgent(BaseAgent):
    def __init__(self):
        self.captured_history = []

    async def ainvoke(self, input: str, chat_history=None):
        pass

    async def astream(self, input: str, chat_history=None):
        self.captured_history = chat_history or []
        yield AIMessageChunk(content="Mock response")

@pytest.mark.asyncio
async def test_stream_chat_response_history_deduplication():
    """
    Test that stream_chat_response correctly removes the last message from history
    if it matches the current user input (to prevent duplication).
    """
    # 1. Setup Mock Objects
    mock_db = AsyncMock()
    # Note: We don't need to configure behavior on mock_db directly because we are patching
    # the DAO functions (message_dao) that use it. The service passes mock_db to the DAO,
    # but the patched DAO functions return our mock data immediately, ignoring the db session object.
    
    mock_agent = MockAgent()
    
    conversation_id = 123
    user_input = "Hello, world!"
    
    request = ChatRequest(
        conversation_id=conversation_id,
        message=user_input,
        model="gemini"
    )

    # 2. Mock Database Behavior
    # Scenario: The DB returns history that includes the message we just saved
    # Since get_messages_by_conversation with limit usually returns newest first (descending),
    # we mock it as such: Newest -> Oldest
    mock_db_history = [
        {"role": "user", "content": user_input},  # <--- The duplicate (Newest)
        {"role": "assistant", "content": "Previous response"},
        {"role": "user", "content": "Previous message"} # Oldest
    ]
    
    # Mock message_dao functions
    # 1. Define Mocks (Define mock behavior before patching)
    mock_create = AsyncMock() # Mock the save operation
    mock_get_msgs = AsyncMock(return_value=mock_db_history)
    
    # Create a MagicMock to represent the dao module/object
    mock_dao = MagicMock()
    mock_dao.create_message = mock_create
    mock_dao.get_messages_by_conversation = mock_get_msgs

    # 2. Patch (Inject our pre-configured mock object using 'new')
    with patch("src.services.chat_service.message_dao", new=mock_dao):

        # 3. Execute Service Function
        # We iterate over the generator to trigger execution
        async for _ in stream_chat_response(request, mock_agent, mock_db):
            pass

        # 4. Verify Results
        
        # Verify save was called
        mock_create.assert_called()
        
        # Verify history passed to agent
        # It should contain: "Previous message", "Previous response"
        # It should NOT contain: "Hello, world!" (the last one)
        
        captured = mock_agent.captured_history
        print(f"\nCaptured History: {captured}")
        
        assert len(captured) == 2, f"Expected 2 messages in history, got {len(captured)}"
        
        assert isinstance(captured[0], HumanMessage)
        assert captured[0].content == "Previous message"
        
        assert isinstance(captured[1], AIMessage)
        assert captured[1].content == "Previous response"
        
        # Explicitly check that the last message is NOT the current input
        if captured:
            assert captured[-1].content != user_input, "Duplicate message was not removed!"

@pytest.mark.asyncio
async def test_stream_chat_response_history_no_duplication_needed():
    """
    Test that history is preserved if the last message does NOT match current input.
    (This technically shouldn't happen in the current implementation flow, but good for robustness check)
    """
    mock_db = AsyncMock()
    mock_agent = MockAgent()
    user_input = "New input"
    
    request = ChatRequest(conversation_id=1, message=user_input, model="gemini")

    # DB returns history that DOES NOT include the current message yet (e.g. race condition or implementation change)
    mock_db_history = [
        {"role": "user", "content": "Old message"}
    ]
    
    # Mock message_dao functions
    mock_create = AsyncMock()
    mock_get_msgs = AsyncMock(return_value=mock_db_history)
    
    mock_dao = MagicMock()
    mock_dao.create_message = mock_create
    mock_dao.get_messages_by_conversation = mock_get_msgs

    with patch("src.services.chat_service.message_dao", new=mock_dao):

        async for _ in stream_chat_response(request, mock_agent, mock_db):
            pass

        captured = mock_agent.captured_history
        assert len(captured) == 1
        assert captured[0].content == "Old message"
