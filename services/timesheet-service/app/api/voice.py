from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.clients.transcription import TranscriptionClient
from app.core.config import get_settings

router = APIRouter(prefix="/voice", tags=["voice"])


@lru_cache
def get_transcription_client() -> TranscriptionClient:
    return TranscriptionClient(get_settings())


@router.post("", response_model=dict)
async def process_voice(
    file: UploadFile = File(...),
    client: TranscriptionClient = Depends(get_transcription_client),
) -> dict:
    try:
        return await client.transcribe(file)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
