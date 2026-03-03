from __future__ import annotations

import json
from datetime import date as dt_date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings
from app.models.schemas import LlmActionExtraction


class GroqClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def extract_action(self, user_message: str, session_state: dict | None = None) -> LlmActionExtraction:
        if not self._settings.groq_api_key or self._settings.groq_api_key == "CHANGE_ME":
            raise RuntimeError(
                "Groq API key is not configured. Set GROQ_API_KEY in services/timesheet-service/.env"
            )

        prompt = self._build_prompt(user_message, session_state or {})
        payload = {
            "model": self._settings.groq_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        response_json = self._call_chat_completions(payload)
        raw_text = self._extract_response_text(response_json)
        parsed = self._parse_llm_json(raw_text)
        return LlmActionExtraction.model_validate(parsed)

    def _call_chat_completions(self, payload: dict) -> dict:
        request = Request(
            self._settings.groq_chat_completions_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._settings.groq_api_key}",
                "User-Agent": self._settings.groq_user_agent,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=60) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403 and "error code: 1010" in details.lower():
                raise RuntimeError(
                    "Groq request blocked by Cloudflare (error 1010). "
                    "Try setting GROQ_USER_AGENT in .env to a browser-like value "
                    "and restart containers. If it still fails, test from another network/IP."
                ) from exc
            raise RuntimeError(f"Groq API HTTP error {exc.code}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Groq API connection error: {exc.reason}") from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Groq API returned invalid JSON") from exc

    @staticmethod
    def _extract_response_text(response_json: dict) -> str:
        choices = response_json.get("choices") or []
        if not choices:
            raise RuntimeError("Groq API response did not contain choices")

        first_choice = choices[0]
        message = first_choice.get("message") or {}
        content = message.get("content")

        if isinstance(content, str) and content.strip():
            return content

        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text

        raise RuntimeError("Groq API response did not contain text output")

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
                    raise RuntimeError("Groq output could not be parsed as JSON") from exc
            raise RuntimeError("Groq output could not be parsed as JSON")

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
