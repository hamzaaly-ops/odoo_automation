from __future__ import annotations

from datetime import date as dt_date
from uuid import uuid4

from pydantic import ValidationError

from app.clients.groq import GroqClient
from app.clients.gemini import GeminiClient
from app.core.chat_session_store import ChatSessionStore
from app.core.config import Settings
from app.models.schemas import (
    ChatQueryResponse,
    TimesheetCreate,
    TimesheetFillWeekRequest,
    TimesheetUpdate,
)
from app.services.timesheet_service import OdooTimesheetService

CREATE_REQUIRED_FIELDS = [
    "description",
    "date",
    "hours",
    "employee_id",
    "project_id",
    "task_id",
]

UPDATE_ALLOWED_FIELDS = [
    "description",
    "date",
    "hours",
    "employee_id",
    "project_id",
    "task_id",
    "user_id",
]

FILL_WEEK_REQUIRED_FIELDS = [
    "employee_id",
    "project_id",
    "task_id",
    "week_start",
]

REQUIRED_BY_ACTION = {
    "create_timesheet": CREATE_REQUIRED_FIELDS,
    "update_timesheet": ["entry_id"],
    "list_timesheets": ["employee_id"],
    "fill_week": FILL_WEEK_REQUIRED_FIELDS,
}

FIELD_PROMPTS = {
    "description": "description (what work was done)",
    "date": "date in YYYY-MM-DD format",
    "hours": "hours worked",
    "employee_id": "employee_id",
    "project_id": "project_id",
    "task_id": "task_id",
    "user_id": "user_id (optional)",
    "entry_id": "timesheet entry_id to update",
    "week_start": "week_start date (Monday) in YYYY-MM-DD format",
    "daily_hours": "daily_hours",
    "description_template": "description_template",
    "weekdays": "weekdays as list (0=Mon ... 6=Sun)",
    "overwrite_existing": "overwrite_existing (true/false)",
    "at_least_one_update_field": (
        "at least one field to update (description/date/hours/project/task/employee/user)"
    ),
}

ACTION_LABELS = {
    "create_timesheet": "create a timesheet",
    "update_timesheet": "update a timesheet",
    "list_timesheets": "list timesheets",
    "fill_week": "fill a week of timesheets",
}


class ChatOrchestratorService:
    def __init__(
        self,
        llm_client: GeminiClient | GroqClient,
        session_store: ChatSessionStore,
        timesheet_service: OdooTimesheetService,
        settings: Settings,
    ):
        self._llm_client = llm_client
        self._session_store = session_store
        self._timesheet_service = timesheet_service
        self._settings = settings

    def handle_query(self, message: str, session_id: str | None = None) -> ChatQueryResponse:
        session_id = session_id or uuid4().hex
        state = self._session_store.get_state(session_id)

        llm_result = self._llm_client.extract_action(message, state)
        action = llm_result.action
        incoming_fields = self._normalize_fields(llm_result.fields)

        pending_action = state.get("pending_action")
        pending_fields = state.get("fields") or {}

        if action == "none" and pending_action and incoming_fields:
            action = pending_action

        if action == "none":
            if pending_action:
                merged_fields = dict(pending_fields)
                missing_fields = self._get_missing_fields(pending_action, merged_fields)
                question = self._build_follow_up_question(pending_action, missing_fields)
                self._session_store.save_state(
                    session_id,
                    {
                        "pending_action": pending_action,
                        "fields": merged_fields,
                        "missing_fields": missing_fields,
                    },
                )
                return ChatQueryResponse(
                    session_id=session_id,
                    status="needs_clarification",
                    assistant_message=question,
                    action=pending_action,
                    missing_fields=missing_fields,
                )

            message_text = (
                llm_result.user_message
                or "I can create, update, list, or auto-fill Odoo timesheets."
            )
            return ChatQueryResponse(
                session_id=session_id,
                status="message",
                assistant_message=message_text,
                action=None,
            )

        if action not in REQUIRED_BY_ACTION:
            return ChatQueryResponse(
                session_id=session_id,
                status="error",
                assistant_message=f"Unsupported action from model: {action}",
                action=action,
            )

        if action != pending_action:
            merged_fields = {}
        else:
            merged_fields = dict(pending_fields)

        merged_fields.update(incoming_fields)

        missing_fields = self._get_missing_fields(action, merged_fields)
        if missing_fields:
            question = self._build_follow_up_question(action, missing_fields)
            self._session_store.save_state(
                session_id,
                {
                    "pending_action": action,
                    "fields": merged_fields,
                    "missing_fields": missing_fields,
                },
            )
            return ChatQueryResponse(
                session_id=session_id,
                status="needs_clarification",
                assistant_message=question,
                action=action,
                missing_fields=missing_fields,
            )

        try:
            result, message_text = self._execute_action(action, merged_fields)
        except (ValidationError, ValueError) as exc:
            self._session_store.save_state(
                session_id,
                {
                    "pending_action": action,
                    "fields": merged_fields,
                    "missing_fields": [],
                },
            )
            return ChatQueryResponse(
                session_id=session_id,
                status="error",
                assistant_message=(
                    "I could not execute that yet. Please correct the details and try again. "
                    f"Reason: {exc}"
                ),
                action=action,
            )
        except Exception as exc:
            return ChatQueryResponse(
                session_id=session_id,
                status="error",
                assistant_message=f"Execution failed: {exc}",
                action=action,
            )

        self._session_store.clear_state(session_id)
        return ChatQueryResponse(
            session_id=session_id,
            status="completed",
            assistant_message=message_text,
            action=action,
            result=result,
        )

    def _execute_action(self, action: str, fields: dict) -> tuple[dict | list[dict], str]:
        if action == "create_timesheet":
            payload = TimesheetCreate.model_validate(fields)
            created = self._timesheet_service.create_timesheet(payload)
            result = created.model_dump(mode="json")
            message = (
                f"Timesheet created successfully with id {created.id} "
                f"for {created.date} ({created.hours} hours)."
            )
            return result, message

        if action == "update_timesheet":
            entry_id = int(fields["entry_id"])
            update_data = {
                key: fields[key]
                for key in UPDATE_ALLOWED_FIELDS
                if key in fields and fields[key] is not None
            }
            payload = TimesheetUpdate.model_validate(update_data)
            updated = self._timesheet_service.update_timesheet(entry_id, payload)
            result = updated.model_dump(mode="json")
            message = f"Timesheet {entry_id} updated successfully."
            return result, message

        if action == "list_timesheets":
            employee_id = int(fields["employee_id"])
            date_from = self._parse_date(fields.get("date_from"))
            date_to = self._parse_date(fields.get("date_to"))
            entries = self._timesheet_service.list_timesheets(
                employee_id=employee_id,
                date_from=date_from,
                date_to=date_to,
            )
            result = [entry.model_dump(mode="json") for entry in entries]
            message = f"Found {len(entries)} timesheet entries."
            return result, message

        if action == "fill_week":
            fill_data = dict(fields)
            fill_data.setdefault("daily_hours", self._settings.default_daily_hours)
            payload = TimesheetFillWeekRequest.model_validate(fill_data)
            filled = self._timesheet_service.fill_week(payload)
            result = filled.model_dump(mode="json")
            message = (
                f"Week fill completed. Created {len(filled.created_entry_ids)}, "
                f"updated {len(filled.updated_entry_ids)}, "
                f"skipped {len(filled.skipped_dates)}."
            )
            return result, message

        raise ValueError(f"Unsupported action: {action}")

    @staticmethod
    def _parse_date(value):
        if value is None:
            return None
        if isinstance(value, dt_date):
            return value
        if isinstance(value, str):
            return dt_date.fromisoformat(value)
        raise ValueError("date must be ISO string (YYYY-MM-DD)")

    @staticmethod
    def _normalize_fields(fields: dict) -> dict:
        normalized = dict(fields or {})

        int_fields = {
            "employee_id",
            "project_id",
            "task_id",
            "user_id",
            "entry_id",
        }
        float_fields = {"hours", "daily_hours"}
        bool_fields = {"overwrite_existing"}

        for key in list(normalized.keys()):
            value = normalized[key]

            if value is None:
                continue

            if key in int_fields:
                converted = ChatOrchestratorService._to_int(value)
                if converted is not None:
                    normalized[key] = converted
                continue

            if key in float_fields:
                converted = ChatOrchestratorService._to_float(value)
                if converted is not None:
                    normalized[key] = converted
                continue

            if key in bool_fields:
                converted = ChatOrchestratorService._to_bool(value)
                if converted is not None:
                    normalized[key] = converted
                continue

            if key == "weekdays" and isinstance(value, str):
                parts = [part.strip() for part in value.split(",") if part.strip()]
                parsed_days = []
                for part in parts:
                    parsed_int = ChatOrchestratorService._to_int(part)
                    if parsed_int is not None:
                        parsed_days.append(parsed_int)
                if parsed_days:
                    normalized[key] = parsed_days

        return normalized

    @staticmethod
    def _to_int(value) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None

    @staticmethod
    def _to_float(value) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            try:
                return float(stripped)
            except ValueError:
                return None
        return None

    @staticmethod
    def _to_bool(value) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped in {"true", "yes", "1"}:
                return True
            if stripped in {"false", "no", "0"}:
                return False
        return None

    @staticmethod
    def _get_missing_fields(action: str, fields: dict) -> list[str]:
        missing = []
        for field in REQUIRED_BY_ACTION[action]:
            value = fields.get(field)
            if value is None or value == "" or value == []:
                missing.append(field)

        if action == "update_timesheet":
            has_updates = any(
                fields.get(field) not in (None, "", []) for field in UPDATE_ALLOWED_FIELDS
            )
            if not has_updates:
                missing.append("at_least_one_update_field")

        return missing

    @staticmethod
    def _build_follow_up_question(action: str, missing_fields: list[str]) -> str:
        action_label = ACTION_LABELS.get(action, action)
        prompts = [FIELD_PROMPTS.get(field, field) for field in missing_fields]

        if not prompts:
            return "Please provide the missing details to continue."

        joined = ", ".join(prompts)
        return f"To {action_label}, I still need: {joined}."
