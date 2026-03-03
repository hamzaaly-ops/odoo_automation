from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from app.clients.groq import GroqClient
from app.api.timesheets import get_timesheet_service
from app.clients.gemini import GeminiClient
from app.core.chat_session_store import ChatSessionStore
from app.core.config import Settings, get_settings
from app.models.schemas import ChatQueryRequest, ChatQueryResponse
from app.services.chat_orchestrator import ChatOrchestratorService
from app.services.timesheet_service import OdooTimesheetService

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@lru_cache
def get_chat_session_store() -> ChatSessionStore:
    return ChatSessionStore(get_settings())


@lru_cache
def get_gemini_client() -> GeminiClient:
    return GeminiClient(get_settings())


@lru_cache
def get_groq_client() -> GroqClient:
    return GroqClient(get_settings())


def get_llm_client(settings: Settings = Depends(get_settings)) -> GeminiClient | GroqClient:
    if settings.llm_provider == "gemini":
        return get_gemini_client()
    return get_groq_client()


def get_chat_orchestrator(
    settings: Settings = Depends(get_settings),
    timesheet_service: OdooTimesheetService = Depends(get_timesheet_service),
    llm_client: GeminiClient | GroqClient = Depends(get_llm_client),
) -> ChatOrchestratorService:
    return ChatOrchestratorService(
        llm_client=llm_client,
        session_store=get_chat_session_store(),
        timesheet_service=timesheet_service,
        settings=settings,
    )


@router.post("/query", response_model=ChatQueryResponse)
def chat_query(
    payload: ChatQueryRequest,
    orchestrator: ChatOrchestratorService = Depends(get_chat_orchestrator),
) -> ChatQueryResponse:
    try:
        return orchestrator.handle_query(
            message=payload.message,
            session_id=payload.session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
