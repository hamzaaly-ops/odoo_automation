from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.chat import ChatQueryResponse, ChatQueryRequest, get_chat_orchestrator
from app.clients.transcription import TranscriptionClient
from app.core.config import get_settings
from app.services.chat_orchestrator import ChatOrchestratorService

router = APIRouter(prefix="/voice", tags=["voice"])


@lru_cache
def get_transcription_client() -> TranscriptionClient:
    return TranscriptionClient(get_settings())


class VoiceQueryResponse(BaseModel):
    transcription: dict[str, str | int | float | dict | list]
    chat: ChatQueryResponse


@router.post("", response_model=dict)
async def process_voice(
    file: UploadFile = File(...),
    client: TranscriptionClient = Depends(get_transcription_client),
) -> dict:
    try:
        return await client.transcribe(file)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/query", response_model=VoiceQueryResponse)
async def process_voice_query(
    file: UploadFile = File(...),
    session_id: str | None = None,
    transcription_client: TranscriptionClient = Depends(get_transcription_client),
    orchestrator: ChatOrchestratorService = Depends(get_chat_orchestrator),
) -> VoiceQueryResponse:
    try:
        transcription = await transcription_client.transcribe(file)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    text = transcription.get("text") or transcription.get("transcript") or transcription.get("message")
    if not text:
        raise HTTPException(status_code=502, detail="Transcription result missing text")

    chat_request = ChatQueryRequest(session_id=session_id, message=text)
    chat_response = orchestrator.handle_query(
        message=chat_request.message,
        session_id=chat_request.session_id,
    )
    return VoiceQueryResponse(transcription=transcription, chat=chat_response)
