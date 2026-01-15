import asyncio
import json
import time
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from langchain_core.messages import AIMessage, HumanMessage

from src.services.llm_service import LLMService
from src.agents.base import BaseAgent
from src.schemas.chat import ChatRequest, PureChatRequest
from src.dao import message_dao
from src.schemas.message import MessageCreateSchema
from src.configs.db import AsyncSessionFactory

from sqlalchemy.exc import InterfaceError, OperationalError

# Defines the maximum number of historical messages to retrieve for context.
MAX_HISTORY_LENGTH = 20

async def save_partial_response_task(conversation_id: int, content: str):
    """Saves a partial assistant response in a background task.

    This is used when a stream is cancelled (e.g., client disconnects) or
    times out, ensuring that whatever the model has generated so far is not lost.
    It creates its own database session to operate independently of the main
    request's session, which might be in a cancelled state.

    Args:
        conversation_id (int): The ID of the conversation to save the message to.
        content (str): The partial content of the assistant's response.
    """
    for attempt in range(3):
        try:
            async with AsyncSessionFactory() as session:
                assistant_message_to_save = MessageCreateSchema(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content,
                )
                await message_dao.create_message(session, message=assistant_message_to_save)
                logger.info(f"Saved partial assistant response in background task: conv={conversation_id} len={len(content)}")
                return  # Success, exit loop
        except (InterfaceError, OperationalError, OSError) as e:
            if attempt < 2:
                logger.warning(f"Failed to save partial response (attempt {attempt + 1}), retrying in 0.1s: {e}")
                await asyncio.sleep(0.1)  # Small delay before retry
            else:
                logger.error(f"Failed to save partial response after {attempt + 1} attempts: {e}")
        except Exception as e:
            logger.error(f"Failed to save partial response due to unexpected error: {e}")
            break  # Don't retry on unknown errors



async def stream_chat_response(
    request: ChatRequest, agent: BaseAgent, db: AsyncSession
):
    """Handles the full chat logic with database interaction.

    This async generator function saves the user's message, retrieves conversation
    history, streams the LLM's response chunk by chunk, and finally saves the
    complete assistant response to the database. It also handles exceptions,
    timeouts, and client disconnections gracefully.

    Args:
        request (ChatRequest): The incoming chat request containing conversation ID and message.
        llm_service (LLMService): The service responsible for interacting with the language model.
        db (AsyncSession): The database session.

    Yields:
        str: Server-Sent Events (SSE) formatted strings, either containing response
             chunks or the final [DONE] message.
    """
    if not agent:
        error_message = "Agent is not available."
        logger.error(error_message)
        error_data = {
            "id": f"chatcmpl-error",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {"content": error_message}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # 1. Save user message
    user_message_to_save = MessageCreateSchema(
        conversation_id=request.conversation_id, role="user", content=request.message
    )
    await message_dao.create_message(db, message=user_message_to_save)

    # 2. Load conversation history from DB
    history_from_db = await message_dao.get_messages_by_conversation(
        db, conversation_id=request.conversation_id, limit=MAX_HISTORY_LENGTH
    )
    
    history_from_db.reverse()

    chat_history = []
    for msg in history_from_db:
        if msg['role'] == 'user':
            chat_history.append(HumanMessage(content=msg['content']))
        elif msg['role'] == 'assistant':
            chat_history.append(AIMessage(content=msg['content']))

    logger.info(f"Initiating true stream for conversation {request.conversation_id} with {len(chat_history)} messages in history.")
    
    full_response_content = ""
    response_saved = False
    try:
        # 3. Call the astream method on the agent with history
        llm_stream = agent.astream(request.message, chat_history=chat_history)
        
        # 4. Iterate over the stream with timeout protection (300 seconds = 5 minutes per chunk)
        stream_iter = llm_stream.__aiter__()
        timeout_seconds = 300.0
        
        while True:
            try:
                chunk_task = asyncio.create_task(stream_iter.__anext__())
                chunk = await asyncio.wait_for(chunk_task, timeout=timeout_seconds)
                
                logger.debug(f"Received chunk of type {type(chunk)}: {chunk}")
                if hasattr(chunk, 'content') and chunk.content:
                    full_response_content += chunk.content
                    
                    chunk_data = {
                        "id": f"chatcmpl-{request.conversation_id}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": request.model,
                        "choices": [{"index": 0, "delta": {"content": chunk.content}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.warning(f"LLM stream timeout for conversation {request.conversation_id}, partial response length={len(full_response_content)}")
                if full_response_content:
                    asyncio.create_task(save_partial_response_task(request.conversation_id, full_response_content))
                    response_saved = True
                    logger.info(f"Triggered background save for partial response due to timeout: conv={request.conversation_id} len={len(full_response_content)}")
                
                timeout_data = {
                    "id": f"chatcmpl-{request.conversation_id}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [{"index": 0, "delta": {"content": "\n\n[Stream timeout after 5 minutes]"}, "finish_reason": "stop"}]
                }
                yield f"data: {json.dumps(timeout_data)}\n\n"
                yield "data: [DONE]\n\n"
                return
        
        logger.info("Streaming finished.")
        yield "data: [DONE]\n\n"
        
        # 5. Save assistant's full response
        if full_response_content:
            logger.info(f"Saving assistant response conv={request.conversation_id} len={len(full_response_content)} preview={full_response_content[:100]}")
            assistant_message_to_save = MessageCreateSchema(
                conversation_id=request.conversation_id,
                role="assistant",
                content=full_response_content,
            )
            await message_dao.create_message(db, message=assistant_message_to_save)
            response_saved = True

    except asyncio.CancelledError:
        logger.warning(f"Stream cancelled (client disconnected) for conversation {request.conversation_id}, partial response length={len(full_response_content)}")
        if full_response_content and not response_saved:
            asyncio.create_task(save_partial_response_task(request.conversation_id, full_response_content))
        raise

    except Exception as e:
        error_message = f"An error occurred during streaming: {e}"
        logger.exception(error_message)
        if full_response_content and not response_saved:
            asyncio.create_task(save_partial_response_task(request.conversation_id, full_response_content))
        
        error_data = {
            "id": f"chatcmpl-{request.conversation_id}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {"content": f"\n\n{error_message}"}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"



async def stream_pure_chat_response(
    request: PureChatRequest, llm_service: LLMService
):
    """Streams an LLM response without any database interaction.

    This is a stateless endpoint useful for quick, non-persistent queries.
    It takes a message and returns the LLM's response directly.

    Args:
        request (PureChatRequest): The request containing the user's message.
        llm_service (LLMService): The service for interacting with the language model.

    Yields:
        str: Server-Sent Events (SSE) formatted strings.
    """
    if not llm_service:
        error_message = "LLM Service is not available."
        logger.error(error_message)
        error_data = {
            "id": "chatcmpl-pure-error",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {"content": error_message}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"
        return

    logger.info(f"Initiating pure stream with message: '{request.message}'")
    
    try:
        llm_stream = llm_service.astream(request.message)
        
        async for chunk in llm_stream:
            if hasattr(chunk, 'content') and chunk.content:
                chunk_data = {
                    "id": "chatcmpl-pure",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [{"index": 0, "delta": {"content": chunk.content}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
        
        logger.info("Pure streaming finished.")
        yield "data: [DONE]\n\n"

    except Exception as e:
        error_message = f"An error occurred during pure streaming: {e}"
        logger.exception(error_message)
        
        error_data = {
            "id": "chatcmpl-pure",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "delta": {"content": f"\n\n{error_message}"}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"
