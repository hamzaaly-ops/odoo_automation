from __future__ import annotations

from datetime import date as dt_date, timedelta
import difflib
import re
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

AUTO_EMPLOYEE_ACTIONS = {
    "create_timesheet",
    "list_timesheets",
    "fill_week",
}

FIELD_KEY_ALIASES = {
    "description": "description",
    "descriptions": "description",
    "employee": "employee",
    "employees": "employee",
    "project": "project",
    "projects": "project",
    "task": "task",
    "tasks": "task",
    "user": "user",
    "users": "user",
}

FIELD_KEY_CANDIDATES = tuple(FIELD_KEY_ALIASES.keys())
FIELD_KEY_PATTERN = re.compile(
    r"^(?P<key>[\w-]+)\s*(?:id)?\s*(?:is|=|:)?\s*(?P<value>.+)$",
    flags=re.IGNORECASE,
)


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
        heuristic_fields = self._normalize_fields(self._extract_fields_from_message(message))
        incoming_fields = self._merge_prefer_existing(incoming_fields, heuristic_fields)

        pending_action = state.get("pending_action")
        pending_fields = state.get("fields") or {}
        pending_hints = state.get("field_hints") or {}
        pending_misses = state.get("field_misses") or {}

        if action == "none" and pending_action and incoming_fields:
            action = pending_action

        if action == "none":
            if pending_action:
                merged_fields = dict(pending_fields)
                missing_fields = self._get_missing_fields(pending_action, merged_fields)
                question = self._build_follow_up_question(
                    pending_action,
                    missing_fields,
                    pending_hints,
                    pending_misses,
                )
                self._session_store.save_state(
                    session_id,
                    {
                        "pending_action": pending_action,
                        "fields": merged_fields,
                        "missing_fields": missing_fields,
                        "field_hints": pending_hints,
                        "field_misses": pending_misses,
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
        merged_fields = self._normalize_fields(merged_fields)
        merged_fields, field_hints, field_misses = self._resolve_reference_fields(action, merged_fields)

        missing_fields = self._get_missing_fields(action, merged_fields)
        field_hints = self._augment_field_hints(
            action=action,
            fields=merged_fields,
            missing_fields=missing_fields,
            field_hints=field_hints,
        )
        if missing_fields:
            question = self._build_follow_up_question(
                action,
                missing_fields,
                field_hints,
                field_misses,
            )
            self._session_store.save_state(
                session_id,
                {
                    "pending_action": action,
                    "fields": merged_fields,
                    "missing_fields": missing_fields,
                    "field_hints": field_hints,
                    "field_misses": field_misses,
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
                    "field_hints": field_hints,
                    "field_misses": field_misses,
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

        alias_fields = {
            "employee": "employee_name",
            "project": "project_name",
            "task": "task_name",
        }
        for alias_key, canonical_key in alias_fields.items():
            if alias_key in normalized and canonical_key not in normalized:
                normalized[canonical_key] = normalized[alias_key]

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
    def _merge_prefer_existing(primary: dict, secondary: dict) -> dict:
        merged = dict(primary)
        for key, value in (secondary or {}).items():
            if key not in merged or merged[key] in (None, "", []):
                merged[key] = value
        return merged

    @staticmethod
    def _extract_fields_from_message(message: str) -> dict:
        text = message.strip()
        lowered = text.lower()
        extracted: dict = {}

        iso_date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if iso_date_match:
            extracted["date"] = iso_date_match.group(1)

        week_start_match = re.search(r"\bweek(?:_start| start)\s*(?:is|=|:)?\s*(\d{4}-\d{2}-\d{2})\b", lowered)
        if week_start_match:
            extracted["week_start"] = week_start_match.group(1)

        today = dt_date.today()
        if "date" not in extracted:
            if re.search(r"\byesterday\b", lowered):
                extracted["date"] = (today - timedelta(days=1)).isoformat()
            elif re.search(r"\btoday\b", lowered):
                extracted["date"] = today.isoformat()
            elif re.search(r"\btomorrow\b", lowered):
                extracted["date"] = (today + timedelta(days=1)).isoformat()

        hours_match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|hr|h)\b", lowered)
        if hours_match:
            extracted["hours"] = float(hours_match.group(1))

        entry_match = re.search(r"\b(?:entry|timesheet)\s*(?:id)?\s*(?:is|=|:)?\s*(\d+)\b", lowered)
        if entry_match:
            extracted["entry_id"] = int(entry_match.group(1))

        chunks = re.split(r"[,\n;]|\band\b", text, flags=re.IGNORECASE)
        for chunk in chunks:
            segment = chunk.strip().strip(".")
            if not segment:
                continue

            key_match = FIELD_KEY_PATTERN.match(segment)
            if not key_match:
                continue

            key = self._normalize_field_label(key_match.group("key"))
            if not key:
                continue

            value = key_match.group("value").strip().strip(".")
            value = re.sub(r"^(?:to\s+)+", "", value, flags=re.IGNORECASE)
            if not value:
                continue

            if key == "description":
                extracted["description"] = value
                continue

            id_key = f"{key}_id"
            name_key = f"{key}_name"
            if value.isdigit():
                extracted[id_key] = int(value)
            else:
                extracted[name_key] = value

        return extracted

    @staticmethod
    def _normalize_field_label(raw_key: str) -> str | None:
        normalized = raw_key.strip().lower()
        if not normalized:
            return None

        direct = FIELD_KEY_ALIASES.get(normalized)
        if direct:
            return direct

        cleaned = re.sub(r"[^a-z0-9]+", "", normalized)
        if cleaned.endswith("id") and len(cleaned) > 2:
            cleaned = cleaned[:-2]

        direct = FIELD_KEY_ALIASES.get(cleaned)
        if direct:
            return direct

        tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
        for token in tokens:
            direct = FIELD_KEY_ALIASES.get(token)
            if direct:
                return direct

        candidate = difflib.get_close_matches(
            cleaned or normalized,
            FIELD_KEY_CANDIDATES,
            n=1,
            cutoff=0.7,
        )
        if candidate:
            return FIELD_KEY_ALIASES.get(candidate[0])

        return None

    def _resolve_reference_fields(
        self,
        action: str,
        fields: dict,
    ) -> tuple[dict, dict[str, list[str]], dict[str, dict[str, list[str]]]]:
        resolved = dict(fields)
        hints: dict[str, list[str]] = {}
        misses: dict[str, dict[str, list[str]]] = {}
        explicit_employee_input = any(
            fields.get(key) not in (None, "", [])
            for key in ["employee_id", "employee_name", "employee"]
        )

        def resolve_field(
            field_key: str,
            name_keys: list[str],
            resolver,
            resolver_kwargs: dict | None = None,
        ) -> None:
            value = resolved.get(field_key)
            if isinstance(value, int):
                return

            lookup = None
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.isdigit():
                    resolved[field_key] = int(stripped)
                    return
                if stripped:
                    lookup = stripped

            if lookup is None:
                for name_key in name_keys:
                    candidate = resolved.get(name_key)
                    if isinstance(candidate, str) and candidate.strip():
                        lookup = candidate.strip()
                        break

            if not lookup:
                return

            args = resolver_kwargs or {}
            resolved_id, candidates = resolver(lookup, **args)
            if resolved_id is not None:
                resolved[field_key] = resolved_id
                return

            formatted = self._format_candidates(candidates)
            if formatted:
                hints[field_key] = formatted
                misses[field_key] = {"value": lookup, "matches": formatted}

        resolve_field(
            "employee_id",
            ["employee_name", "employee"],
            self._timesheet_service.resolve_employee_id,
        )
        if (
            action in AUTO_EMPLOYEE_ACTIONS
            and not explicit_employee_input
            and not isinstance(resolved.get("employee_id"), int)
        ):
            try:
                resolved["employee_id"] = self._timesheet_service.get_default_employee_id()
            except Exception:
                pass

        resolve_field(
            "project_id",
            ["project_name", "project"],
            self._timesheet_service.resolve_project_id,
        )

        project_id = resolved.get("project_id")
        task_resolver_kwargs = {}
        if isinstance(project_id, int):
            task_resolver_kwargs["project_id"] = project_id

        resolve_field(
            "task_id",
            ["task_name", "task"],
            self._timesheet_service.resolve_task_id,
            task_resolver_kwargs,
        )

        if action == "fill_week" and "daily_hours" not in resolved and "hours" in resolved:
            resolved["daily_hours"] = resolved.get("hours")

        return resolved, hints, misses

    def _augment_field_hints(
        self,
        action: str,
        fields: dict,
        missing_fields: list[str],
        field_hints: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        if "project_id" in missing_fields and "project_id" not in field_hints:
            project_suggestions = self._timesheet_service.suggest_projects()
            if project_suggestions:
                field_hints = dict(field_hints)
                field_hints["project_id"] = project_suggestions

        if "task_id" in missing_fields:
            project_id = fields.get("project_id")
            if isinstance(project_id, int) and "task_id" not in field_hints:
                suggestions = self._timesheet_service.suggest_tasks_for_project(project_id)
                if suggestions:
                    field_hints = dict(field_hints)
                    field_hints["task_id"] = suggestions

        return field_hints

    @staticmethod
    def _format_candidates(candidates: list[dict], limit: int = 8) -> list[str]:
        formatted: list[str] = []
        for candidate in candidates[:limit]:
            rec_id = candidate.get("id")
            name = candidate.get("name")
            if rec_id is None:
                continue
            if name:
                formatted.append(f"{rec_id}: {name}")
            else:
                formatted.append(str(rec_id))
        return formatted

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
    def _build_follow_up_question(
        action: str,
        missing_fields: list[str],
        field_hints: dict[str, list[str]] | None = None,
        field_miss_info: dict[str, dict[str, list[str]]] | None = None,
    ) -> str:
        action_label = ACTION_LABELS.get(action, action)
        prompts = [FIELD_PROMPTS.get(field, field) for field in missing_fields]

        if not prompts:
            return "Please provide the missing details to continue."

        joined = ", ".join(prompts)
        message = (
            f"To {action_label}, I still need: {joined}. "
            "Reply with the same session_id to continue this thread."
        )

        if not field_hints:
            return message

        hint_parts = []
        for field in missing_fields:
            hints = field_hints.get(field)
            if hints:
                label = FIELD_PROMPTS.get(field, field)
                hint_parts.append(f"{label}: {', '.join(hints)}")

        if not hint_parts:
            return message

        clarification_parts = []
        if field_miss_info:
            for field in missing_fields:
                info = field_miss_info.get(field)
                hints = field_hints.get(field)
                if not hints or not info:
                    continue
                label = FIELD_PROMPTS.get(field, field)
                raw_value = info.get("value")
                if raw_value:
                    clarification_parts.append(
                        f"For {label} I heard \"{raw_value}\"; did you mean {', '.join(hints)}?"
                    )

        base_message = f"{message} I found these matches: {' | '.join(hint_parts)}."
        if clarification_parts:
            return f"{base_message} {' '.join(clarification_parts)} Did you mean one of them?"
        return base_message
