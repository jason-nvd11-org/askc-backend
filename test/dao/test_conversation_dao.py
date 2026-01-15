import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Ensure config is loaded before other imports
import src.configs.config
from src.configs.db import DATABASE_URL, get_async_engine
from src.models.tables import metadata
from src.schemas.user import UserCreateSchema
from src.schemas.conversation import ConversationCreateSchema
from src.schemas.message import MessageCreateSchema
from src.dao import user_dao, conversation_dao, message_dao

@pytest.fixture(scope="function")
async def managed_db_session():
    """
    A fixture that provides a clean database and a session for each test.
    """
    # Clear the cache to ensure we get a new engine bound to the current event loop
    # This fixes the "Event loop is closed" error when running multiple async tests
    get_async_engine.cache_clear()
    
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(engine) as session:
        yield session
    
    await engine.dispose()

@pytest.mark.asyncio
async def test_create_and_get_conversations(managed_db_session: AsyncSession):
    """
    Test creating and retrieving conversations for a user.
    """
    # 1. Create a user first
    user_to_create = UserCreateSchema(username="conv_test_user")
    created_user = await user_dao.create_user(managed_db_session, user=user_to_create)
    user_id = created_user["id"]

    # 2. Create two conversations for this user, one with a name
    conv_to_create_1 = ConversationCreateSchema(user_id=user_id, name="Test Conversation 1")
    created_conv_1 = await conversation_dao.create_conversation(managed_db_session, conv=conv_to_create_1)

    # Create messages for conversation 1 to test preview
    msg1 = MessageCreateSchema(conversation_id=created_conv_1["id"], role="user", content="First user message")
    await message_dao.create_message(managed_db_session, msg1)
    
    msg2 = MessageCreateSchema(conversation_id=created_conv_1["id"], role="assistant", content="First AI response")
    await message_dao.create_message(managed_db_session, msg2)

    msg3 = MessageCreateSchema(conversation_id=created_conv_1["id"], role="user", content="Second user message")
    await message_dao.create_message(managed_db_session, msg3)

    conv_to_create_2 = ConversationCreateSchema(user_id=user_id)
    await conversation_dao.create_conversation(managed_db_session, conv=conv_to_create_2)

    # 3. Retrieve the conversations for this user
    conversations = await conversation_dao.get_conversations_by_user(managed_db_session, user_id=user_id)

    # 4. Assertions
    assert conversations is not None
    assert len(conversations) == 2
    assert conversations[0]["user_id"] == user_id
    assert conversations[1]["user_id"] == user_id
    
    # Find the named conversation and assert its name and preview
    named_conv = None
    for conv in conversations:
        if conv["id"] == created_conv_1["id"]:
            named_conv = conv
            break
    
    assert named_conv is not None
    assert named_conv["name"] == "Test Conversation 1"
    assert named_conv["preview"] == "First user message"

@pytest.mark.asyncio
async def test_get_conversations_for_user_with_no_conversations(managed_db_session: AsyncSession):
    """
    Test retrieving conversations for a user who has none.
    """
    # 1. Create a user
    user_to_create = UserCreateSchema(username="no_conv_user")
    created_user = await user_dao.create_user(managed_db_session, user=user_to_create)
    user_id = created_user["id"]

    # 2. Retrieve conversations for this user
    conversations = await conversation_dao.get_conversations_by_user(managed_db_session, user_id=user_id)

    # 3. Assertions
    assert conversations is not None
    assert len(conversations) == 0
