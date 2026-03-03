from __future__ import annotations

import json
from datetime import date as dt_date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings
from app.models.schemas import LlmActionExtraction


class GeminiClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def extract_action(self, user_message: str, session_state: dict | None = None) -> LlmActionExtraction:
        if not self._settings.gemini_api_key or self._settings.gemini_api_key == "CHANGE_ME":
            raise RuntimeError(
                "Gemini API key is not configured. Set GEMINI_API_KEY in services/timesheet-service/.env"
            )

        prompt = self._build_prompt(user_message, session_state or {})
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        response_json = self._call_generate_content(payload)
        raw_text = self._extract_response_text(response_json)
        parsed = self._parse_llm_json(raw_text)
        return LlmActionExtraction.model_validate(parsed)

    def _call_generate_content(self, payload: dict) -> dict:
        url = (
            f"{self._settings.gemini_generate_content_url}"
            f"?key={self._settings.gemini_api_key}"
        )

        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=60) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API HTTP error {exc.code}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Gemini API connection error: {exc.reason}") from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini API returned invalid JSON") from exc

    @staticmethod
    def _extract_response_text(response_json: dict) -> str:
        candidates = response_json.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini API response did not contain candidates")

        first_candidate = candidates[0]
        content = first_candidate.get("content") or {}
        parts = content.get("parts") or []

        for part in parts:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text

        raise RuntimeError("Gemini API response did not contain text output")

    @staticmethod
    def _parse_llm_json(raw_text: str) -> dict:
        text = raw_text.strip()

        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                snippet = text[start : end + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Gemini output could not be parsed as JSON") from exc
            raise RuntimeError("Gemini output could not be parsed as JSON")

    @staticmethod
    def _build_prompt(user_message: str, session_state: dict) -> str:
        current_date = dt_date.today().isoformat()
        context_json = json.dumps(session_state, ensure_ascii=True)

        return f"""
You extract actionable intent for an Odoo timesheet assistant.
Return ONLY valid JSON with this exact shape:
{{
  "action": "none|create_timesheet|update_timesheet|list_timesheets|fill_week",
  "fields": {{}},
  "user_message": "optional short text"
}}

Rules:
- Never return markdown.
- Never invent employee/project/task/user IDs.
- If user mentions a date naturally, convert to YYYY-MM-DD.
- For weekdays in fill_week, use integers where Monday=0 and Sunday=6.
- If this is just a normal chat question, use action="none".
- If the user is answering a missing-field follow-up, return the relevant fields.

Current date: {current_date}
Session context JSON: {context_json}
User message: {user_message}
""".strip()
