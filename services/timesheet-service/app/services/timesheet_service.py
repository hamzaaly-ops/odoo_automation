from __future__ import annotations

import difflib
from datetime import timedelta
import re

from app.clients.odoo import OdooClient
from app.models.schemas import (
    TimesheetCreate,
    TimesheetFillWeekRequest,
    TimesheetFillWeekResponse,
    TimesheetRead,
    TimesheetUpdate,
)


def _many2one_id(value) -> int | None:
    if isinstance(value, list) and value:
        return int(value[0])
    if isinstance(value, tuple) and value:
        return int(value[0])
    if isinstance(value, int):
        return value
    return None


class OdooTimesheetService:
    def __init__(self, client: OdooClient):
        self._client = client

    def create_timesheet(self, payload: TimesheetCreate) -> TimesheetRead:
        resolved_employee_id = self._resolve_employee_id(payload.employee_id)
        self._validate_common_references(
            employee_id=resolved_employee_id,
            project_id=payload.project_id,
            task_id=payload.task_id,
            user_id=payload.user_id,
            check_task_project_pair=True,
        )
        payload_with_employee = payload.model_copy(update={"employee_id": resolved_employee_id})
        entry_id = self._client.create_timesheet(payload_with_employee)
        record = self._client.get_timesheet(entry_id)

        if record is None:
            raise RuntimeError(f"Created timesheet {entry_id} but failed to fetch it")

        return self._map_record(record)

    def update_timesheet(self, entry_id: int, payload: TimesheetUpdate) -> TimesheetRead:
        if not self._client.record_exists("account.analytic.line", entry_id):
            raise ValueError(f"Timesheet entry_id {entry_id} does not exist in Odoo.")

        update_payload = payload
        if payload.employee_id is not None:
            resolved_employee_id = self._resolve_employee_id(payload.employee_id)
            update_payload = payload.model_copy(update={"employee_id": resolved_employee_id})

        self._validate_common_references(
            employee_id=update_payload.employee_id,
            project_id=update_payload.project_id,
            task_id=update_payload.task_id,
            user_id=update_payload.user_id,
            check_task_project_pair=(
                update_payload.project_id is not None and update_payload.task_id is not None
            ),
        )
        self._client.update_timesheet(entry_id, update_payload)
        record = self._client.get_timesheet(entry_id)

        if record is None:
            raise RuntimeError(f"Timesheet {entry_id} not found after update")

        return self._map_record(record)

    def list_timesheets(
        self,
        employee_id: int | None,
        date_from=None,
        date_to=None,
    ) -> list[TimesheetRead]:
        resolved_employee_id = self._resolve_employee_id(employee_id)
        records = self._client.list_timesheets(resolved_employee_id, date_from, date_to)
        return [self._map_record(record) for record in records]

    def fill_week(self, payload: TimesheetFillWeekRequest) -> TimesheetFillWeekResponse:
        resolved_employee_id = self._resolve_employee_id(payload.employee_id)
        self._validate_common_references(
            employee_id=resolved_employee_id,
            project_id=payload.project_id,
            task_id=payload.task_id,
            user_id=payload.user_id,
            check_task_project_pair=True,
        )

        created_entry_ids: list[int] = []
        updated_entry_ids: list[int] = []
        skipped_dates = []

        for weekday in payload.weekdays:
            entry_date = payload.week_start + timedelta(days=weekday)
            existing_entries = self._client.list_entries_for_day(
                resolved_employee_id,
                entry_date,
                payload.project_id,
                payload.task_id,
            )

            try:
                description = payload.description_template.format(date=entry_date.isoformat())
            except KeyError as exc:
                raise ValueError(
                    "description_template may only use the {date} placeholder"
                ) from exc

            if existing_entries and not payload.overwrite_existing:
                skipped_dates.append(entry_date)
                continue

            if existing_entries and payload.overwrite_existing:
                existing_id = int(existing_entries[0]["id"])
                self._client.update_timesheet(
                    existing_id,
                    TimesheetUpdate(
                        description=description,
                        date=entry_date,
                        hours=payload.daily_hours,
                        employee_id=resolved_employee_id,
                        project_id=payload.project_id,
                        task_id=payload.task_id,
                        user_id=payload.user_id,
                    ),
                )
                updated_entry_ids.append(existing_id)
                continue

            created_id = self._client.create_timesheet(
                TimesheetCreate(
                    description=description,
                    date=entry_date,
                    hours=payload.daily_hours,
                    employee_id=resolved_employee_id,
                    project_id=payload.project_id,
                    task_id=payload.task_id,
                    user_id=payload.user_id,
                )
            )
            created_entry_ids.append(created_id)

        return TimesheetFillWeekResponse(
            created_entry_ids=created_entry_ids,
            updated_entry_ids=updated_entry_ids,
            skipped_dates=skipped_dates,
        )

    def _validate_common_references(
        self,
        employee_id: int | None,
        project_id: int | None,
        task_id: int | None,
        user_id: int | None,
        check_task_project_pair: bool = False,
    ) -> None:
        if employee_id is not None:
            self._ensure_employee_exists(employee_id)
        if project_id is not None:
            self._ensure_project_exists(project_id)
        if task_id is not None:
            self._ensure_task_exists(task_id)
        if user_id is not None:
            self._ensure_user_exists(user_id)

        if check_task_project_pair and project_id is not None and task_id is not None:
            if not self._client.task_belongs_to_project(task_id, project_id):
                raise ValueError(
                    f"task_id {task_id} does not belong to project_id {project_id}."
                )

    def get_default_employee_id(self) -> int:
        employee_id = self._client.get_current_employee_id()
        self._ensure_employee_exists(employee_id)
        return employee_id

    def _resolve_employee_id(self, employee_id: int | None) -> int:
        if employee_id is None:
            return self.get_default_employee_id()
        if self._client.record_exists("hr.employee", employee_id):
            return employee_id

        linked_employee_id = self._client.find_employee_id_by_user_id(employee_id)
        if linked_employee_id is not None:
            return linked_employee_id

        raise ValueError(
            f"employee_id {employee_id} does not exist in Odoo."
        )

    def _ensure_employee_exists(self, employee_id: int) -> None:
        if not self._client.record_exists("hr.employee", employee_id):
            raise ValueError(f"employee_id {employee_id} does not exist in Odoo.")

    def _ensure_project_exists(self, project_id: int) -> None:
        if not self._client.record_exists("project.project", project_id):
            raise ValueError(f"project_id {project_id} does not exist in Odoo.")

    def _ensure_task_exists(self, task_id: int) -> None:
        if not self._client.record_exists("project.task", task_id):
            raise ValueError(f"task_id {task_id} does not exist in Odoo.")

    def _ensure_user_exists(self, user_id: int) -> None:
        if not self._client.record_exists("res.users", user_id):
            raise ValueError(f"user_id {user_id} does not exist in Odoo.")

    def resolve_project_id(self, query: str) -> tuple[int | None, list[dict]]:
        resolved_id, ranked = self._resolve_name_to_id(
            query=query,
            candidates=self._client.find_project_candidates(query, limit=25),
        )
        if resolved_id is not None:
            return resolved_id, ranked[:10]

        all_projects = self._client.list_all_projects()
        resolved_id, ranked_all = self._resolve_name_to_id(query=query, candidates=all_projects)
        if resolved_id is not None:
            return resolved_id, ranked_all[:10]

        if ranked:
            return None, ranked[:10]
        return None, ranked_all[:10]

    def resolve_employee_id(self, query: str) -> tuple[int | None, list[dict]]:
        resolved_id, ranked = self._resolve_name_to_id(
            query=query,
            candidates=self._client.find_employee_candidates(query, limit=25),
        )
        if resolved_id is not None:
            return resolved_id, ranked[:10]

        all_employees = self._client.list_all_employees()
        resolved_id, ranked_all = self._resolve_name_to_id(query=query, candidates=all_employees)
        if resolved_id is not None:
            return resolved_id, ranked_all[:10]

        if ranked:
            return None, ranked[:10]
        return None, ranked_all[:10]

    def resolve_task_id(
        self,
        query: str,
        project_id: int | None = None,
    ) -> tuple[int | None, list[dict]]:
        resolved_id, ranked = self._resolve_name_to_id(
            query=query,
            candidates=self._client.find_task_candidates(query, project_id=project_id, limit=25),
        )
        if resolved_id is not None:
            return resolved_id, ranked[:10]

        all_tasks = self._client.list_all_tasks(project_id=project_id)
        resolved_id, ranked_all = self._resolve_name_to_id(query=query, candidates=all_tasks)
        if resolved_id is not None:
            return resolved_id, ranked_all[:10]

        if ranked:
            return None, ranked[:10]
        return None, ranked_all[:10]

    def suggest_tasks_for_project(self, project_id: int, limit: int = 8) -> list[str]:
        records = self._client.list_tasks_for_project(project_id, limit=limit)
        suggestions = []
        for record in records:
            rec_id = int(record["id"])
            name = str(record.get("name") or "")
            suggestions.append(f"{rec_id}: {name}")
        return suggestions

    def suggest_projects(self, limit: int = 8) -> list[str]:
        records = self._client.list_all_projects()
        suggestions = []
        for record in records[:limit]:
            rec_id = int(record["id"])
            name = str(record.get("name") or "")
            suggestions.append(f"{rec_id}: {name}")
        return suggestions

    @staticmethod
    def _resolve_name_to_id(query: str, candidates: list[dict]) -> tuple[int | None, list[dict]]:
        if not candidates:
            return None, []

        normalized_query = OdooTimesheetService._name_keys(query)
        exact_matches: list[dict] = []

        for candidate in candidates:
            candidate_name = str(candidate.get("name") or "")
            candidate_keys = OdooTimesheetService._name_keys(candidate_name)
            if normalized_query & candidate_keys:
                exact_matches.append(candidate)

        if len(exact_matches) == 1:
            best_id = int(exact_matches[0]["id"])
            ordered = [exact_matches[0]]
            seen = {best_id}
            for candidate in candidates:
                candidate_id = int(candidate["id"])
                if candidate_id in seen:
                    continue
                seen.add(candidate_id)
                ordered.append(candidate)
            return best_id, ordered

        scored_candidates: list[tuple[float, dict]] = []
        for candidate in candidates:
            candidate_name = str(candidate.get("name") or "")
            if not candidate_name:
                continue
            score = OdooTimesheetService._candidate_score(query, candidate_name)
            scored_candidates.append((score, candidate))

        if not scored_candidates:
            return None, []

        scored_candidates.sort(
            key=lambda item: (item[0], str(item[1].get("name") or "").lower()),
            reverse=True,
        )
        ranked = [item[1] for item in scored_candidates]

        best_score, best_candidate = scored_candidates[0]
        second_score = scored_candidates[1][0] if len(scored_candidates) > 1 else 0.0

        high_confidence = best_score >= 0.84
        strong_gap = (best_score - second_score) >= 0.08
        single_candidate_confident = len(scored_candidates) == 1 and best_score >= 0.75
        if (high_confidence and strong_gap) or single_candidate_confident:
            return int(best_candidate["id"]), ranked

        return None, ranked

    @staticmethod
    def _candidate_score(query: str, candidate_name: str) -> float:
        query_keys = OdooTimesheetService._name_keys(query)
        candidate_keys = OdooTimesheetService._name_keys(candidate_name)
        if not query_keys or not candidate_keys:
            return 0.0

        best_ratio = 0.0
        contains_bonus = 0.0
        for query_key in query_keys:
            for candidate_key in candidate_keys:
                ratio = difflib.SequenceMatcher(a=query_key, b=candidate_key).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                if query_key in candidate_key or candidate_key in query_key:
                    contains_bonus = max(contains_bonus, 1.0)

        query_tokens = OdooTimesheetService._name_tokens(query)
        candidate_tokens = OdooTimesheetService._name_tokens(candidate_name)
        token_score = 0.0
        if query_tokens and candidate_tokens:
            token_score = len(query_tokens & candidate_tokens) / len(query_tokens)

        score = (best_ratio * 0.70) + (token_score * 0.20) + (contains_bonus * 0.10)
        return min(1.0, score)

    @staticmethod
    def _name_keys(value: str) -> set[str]:
        lowered = value.strip().lower()
        if not lowered:
            return set()

        keys: set[str] = set()
        keys.add(re.sub(r"[^a-z0-9]", "", lowered))

        word_to_digit = {
            "zero": "0",
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
            "ten": "10",
        }

        replaced = lowered
        for word, digit in word_to_digit.items():
            replaced = re.sub(rf"\b{word}\b", digit, replaced)
        keys.add(re.sub(r"[^a-z0-9]", "", replaced))

        for word, digit in word_to_digit.items():
            if lowered.startswith(word) and len(lowered) > len(word):
                prefixed = f"{digit}{lowered[len(word):]}"
                keys.add(re.sub(r"[^a-z0-9]", "", prefixed))

        return {key for key in keys if key}

    @staticmethod
    def _name_tokens(value: str) -> set[str]:
        lowered = value.strip().lower()
        if not lowered:
            return set()

        lowered = lowered.replace("_", " ").replace("-", " ")
        raw_tokens = [token for token in re.split(r"\s+", lowered) if token]

        word_to_digit = {
            "zero": "0",
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
            "ten": "10",
        }

        tokens: set[str] = set()
        for token in raw_tokens:
            stripped = re.sub(r"[^a-z0-9]", "", token)
            if stripped:
                tokens.add(stripped)

            for word, digit in word_to_digit.items():
                if stripped == word:
                    tokens.add(digit)
                if stripped.startswith(word) and len(stripped) > len(word):
                    tokens.add(f"{digit}{stripped[len(word):]}")

        return tokens

    @staticmethod
    def _map_record(record: dict) -> TimesheetRead:
        employee_id = _many2one_id(record.get("employee_id"))
        project_id = _many2one_id(record.get("project_id"))
        task_id = _many2one_id(record.get("task_id"))
        user_id = _many2one_id(record.get("user_id"))

        if employee_id is None or project_id is None or task_id is None:
            raise RuntimeError(f"Invalid Odoo record format: {record}")

        return TimesheetRead(
            id=int(record["id"]),
            description=str(record.get("name") or ""),
            date=record["date"],
            hours=float(record.get("unit_amount") or 0),
            employee_id=employee_id,
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
        )
