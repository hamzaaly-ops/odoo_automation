from __future__ import annotations

from typing import Any

import httpx
from fastapi import UploadFile

from app.core.config import Settings


class TranscriptionClient:
    def __init__(self, settings: Settings):
        self._url = str(settings.transcription_url).rstrip("/")
        self._timeout = httpx.Timeout(60.0, read=60.0)

    async def transcribe(self, upload: UploadFile) -> dict[str, Any]:
        filename = upload.filename or "audio"
        content_type = upload.content_type or "application/octet-stream"
        content = await upload.read()

        files = {
            "file": (
                filename,
                content,
                content_type,
            )
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, files=files)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Transcription API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Transcription request failed: {exc}") from exc

        return response.json()
