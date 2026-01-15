from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.llm.deepseek_chat_model import get_deepseek_llm
from src.llm.gemini_chat_model import get_gemini_llm
from src.services.llm_service import LLMService
from src.configs.db import get_db_session
from src.schemas.chat import ChatRequest, PureChatRequest
from src.services import chat_service
from src.dao import conversation_dao
from src.agents.main_agent import MainAgent
from src.agents.github_react_agent import GithubReactAgent

from src.routers.dependencies import validate_token_and_get_user

from src.schemas.user import UserSchema

# Create an API router
router = APIRouter(
    prefix="/api/v1",
    tags=["Chat"],
)

# --- API Endpoint ---
@router.post("/chat")
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: UserSchema = Depends(validate_token_and_get_user),
):
    """
    Receives a user message, saves it, retrieves conversation history,
    and returns the model's response as a stream of Server-Sent Events (SSE).
    The assistant's final response is also saved to the database.
    """
    # Security Fix: Verify the user owns the conversation before proceeding.
    conversation = await conversation_dao.get_conversation(db, request.conversation_id)
    if not conversation or conversation['user_id'] != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this conversation.")

    logger.info(f"Received chat request for conv {request.conversation_id} with model: {request.model}")

    try:
        if request.model == "gemini":
            llm = get_gemini_llm()
        elif request.model == "deepseek":
            llm = get_deepseek_llm()
        else:
            raise HTTPException(status_code=400, detail=f"Invalid model '{request.model}'. Please use 'gemini' or 'deepseek'.")
        
        llm_service = LLMService(llm=llm)
        
        # Initialize the Agent
        github_agent = GithubReactAgent(llm_service=llm_service)
        # Note: We are initializing the agent for every request here. 
        # In production, this might be optimized or cached, but for now it ensures freshness.
        await github_agent.initialize()
        
        main_agent = MainAgent(llm_service=llm_service, github_agent=github_agent)

    except Exception as e:
        logger.error(f"Failed to initialize Agent/LLM service for request: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize Agent/LLM service.")

    return StreamingResponse(
        chat_service.stream_chat_response(request, main_agent, db),
        media_type="text/event-stream"
    )

@router.post("/purechat")
async def pure_chat(
    request: PureChatRequest,
    current_user: UserSchema = Depends(validate_token_and_get_user),
):
    """
    Receives a user message and directly returns the model's response as a 
    stream of Server-Sent Events (SSE) without any database interaction.
    """
    logger.info(f"Received pure chat request with model: {request.model}")
    
    try:
        if request.model == "gemini":
            llm = get_gemini_llm()
        elif request.model == "deepseek":
            llm = get_deepseek_llm()
        else:
            raise HTTPException(status_code=400, detail=f"Invalid model '{request.model}'. Please use 'gemini' or 'deepseek'.")

        llm_service = LLMService(llm=llm)

    except Exception as e:
        logger.error(f"Failed to initialize LLM service for request: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize LLM service.")

    return StreamingResponse(
        chat_service.stream_pure_chat_response(request, llm_service),
        media_type="text/event-stream"
    )
