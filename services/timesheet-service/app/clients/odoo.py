from __future__ import annotations

from datetime import date
import json
import random
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings
from app.models.schemas import TimesheetCreate, TimesheetUpdate


class OdooClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._uid: int | None = None
        self._employee_id: int | None = None
        self._jsonrpc_endpoint = f"{settings.odoo_base_url}/jsonrpc"

    def authenticate(self) -> int:
        uid = self._jsonrpc_call(
            service="common",
            method="authenticate",
            args=[
                self._settings.odoo_db,
                self._settings.odoo_username,
                self._settings.odoo_password,
                {},
            ],
        )
        if not uid:
            raise RuntimeError(
                "Odoo authentication failed for "
                f"db='{self._settings.odoo_db}', username='{self._settings.odoo_username}'. "
                "Verify ODOO_DB/ODOO_USERNAME/ODOO_PASSWORD."
            )
        self._uid = int(uid)
        return self._uid

    def _ensure_uid(self) -> int:
        if self._uid is None:
            return self.authenticate()
        return self._uid

    def get_current_employee_id(self) -> int:
        if self._employee_id is not None:
            return self._employee_id

        uid = self._ensure_uid()
        resolved_id = self.find_employee_id_by_user_id(uid)
        if resolved_id is None:
            raise RuntimeError(
                "Could not resolve employee_id for authenticated Odoo user "
                f"uid={uid} ({self._settings.odoo_username}). "
                "Link this user to an employee record in hr.employee."
            )

        self._employee_id = int(resolved_id)
        return self._employee_id

    def find_employee_id_by_user_id(self, user_id: int) -> int | None:
        records = self.execute_kw(
            "hr.employee",
            "search_read",
            [[["user_id", "=", int(user_id)]]],
            {
                "fields": ["id"],
                "order": "id asc",
                "limit": 1,
                "context": {"active_test": False},
            },
        )
        if not records:
            return None
        return int(records[0]["id"])

    def execute_kw(
        self,
        model: str,
        method: str,
        args: list | None = None,
        kwargs: dict | None = None,
    ):
        uid = self._ensure_uid()
        args = args or []
        kwargs = kwargs or {}

        return self._jsonrpc_call(
            service="object",
            method="execute_kw",
            args=[
                self._settings.odoo_db,
                uid,
                self._settings.odoo_password,
                model,
                method,
                args,
                kwargs,
            ],
        )

    def create_timesheet(self, payload: TimesheetCreate) -> int:
        employee_id = (
            int(payload.employee_id)
            if payload.employee_id is not None
            else self.get_current_employee_id()
        )
        values = {
            "name": payload.description,
            "date": payload.date.isoformat(),
            "unit_amount": payload.hours,
            "employee_id": employee_id,
            "project_id": payload.project_id,
            "task_id": payload.task_id,
        }

        if payload.user_id is not None:
            values["user_id"] = payload.user_id

        entry_id = self.execute_kw(
            "account.analytic.line",
            "create",
            [values],
        )
        return int(entry_id)

    def update_timesheet(self, entry_id: int, payload: TimesheetUpdate) -> bool:
        values: dict[str, int | str | float] = {}

        if payload.description is not None:
            values["name"] = payload.description
        if payload.date is not None:
            values["date"] = payload.date.isoformat()
        if payload.hours is not None:
            values["unit_amount"] = payload.hours
        if payload.employee_id is not None:
            values["employee_id"] = payload.employee_id
        if payload.project_id is not None:
            values["project_id"] = payload.project_id
        if payload.task_id is not None:
            values["task_id"] = payload.task_id
        if payload.user_id is not None:
            values["user_id"] = payload.user_id

        if not values:
            raise ValueError("No fields were provided for update")

        updated = self.execute_kw(
            "account.analytic.line",
            "write",
            [[entry_id], values],
        )
        return bool(updated)

    def get_timesheet(self, entry_id: int) -> dict | None:
        records = self.execute_kw(
            "account.analytic.line",
            "search_read",
            [[("id", "=", entry_id)]],
            {
                "fields": [
                    "id",
                    "name",
                    "date",
                    "unit_amount",
                    "employee_id",
                    "project_id",
                    "task_id",
                    "user_id",
                ],
                "limit": 1,
            },
        )
        if not records:
            return None
        return records[0]

    def list_timesheets(
        self,
        employee_id: int | None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        resolved_employee_id = (
            int(employee_id) if employee_id is not None else self.get_current_employee_id()
        )
        domain: list[list[str | int]] = [["employee_id", "=", resolved_employee_id]]

        if date_from is not None:
            domain.append(["date", ">=", date_from.isoformat()])
        if date_to is not None:
            domain.append(["date", "<=", date_to.isoformat()])

        records = self.execute_kw(
            "account.analytic.line",
            "search_read",
            [domain],
            {
                "fields": [
                    "id",
                    "name",
                    "date",
                    "unit_amount",
                    "employee_id",
                    "project_id",
                    "task_id",
                    "user_id",
                ],
                "order": "date asc,id asc",
            },
        )
        return list(records)

    def record_exists(self, model: str, record_id: int) -> bool:
        domain = [["id", "=", int(record_id)]]
        count = self.execute_kw(
            model,
            "search_count",
            [domain],
            {
                "context": {"active_test": False},
            },
        )
        return int(count) > 0

    def task_belongs_to_project(self, task_id: int, project_id: int) -> bool:
        domain = [
            ["id", "=", int(task_id)],
            ["project_id", "=", int(project_id)],
        ]
        count = self.execute_kw(
            "project.task",
            "search_count",
            [domain],
            {
                "context": {"active_test": False},
            },
        )
        return int(count) > 0

    def _jsonrpc_call(self, service: str, method: str, args: list):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": service,
                "method": method,
                "args": args,
            },
            "id": random.randint(1, 999_999_999),
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self._jsonrpc_endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=30) as response:
                raw_response = response.read().decode("utf-8")
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Odoo JSON-RPC HTTP error {exc.code}: {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Odoo JSON-RPC connection error: {exc.reason}") from exc

        try:
            response_json = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Odoo JSON-RPC returned invalid JSON") from exc

        if "error" in response_json:
            error = response_json["error"]
            message = error.get("message", "Unknown Odoo JSON-RPC error")
            data = error.get("data")
            if data:
                formatted_detail = self._format_error_data(data)
                if formatted_detail:
                    raise RuntimeError(f"Odoo JSON-RPC error: {formatted_detail}")
                raise RuntimeError(f"Odoo JSON-RPC error: {message}")
            raise RuntimeError(f"Odoo JSON-RPC error: {message}")

        if "result" not in response_json:
            raise RuntimeError("Odoo JSON-RPC response missing 'result'")

        return response_json["result"]

    @staticmethod
    def _format_error_data(data) -> str | None:
        if isinstance(data, dict):
            error_name = str(data.get("name") or "").strip()
            error_message = str(data.get("message") or "").strip()
            if error_name and error_message:
                return f"{error_name}: {error_message}"
            if error_message:
                return error_message
            if error_name:
                return error_name
            return None

        if data is None:
            return None

        detail = str(data).strip()
        return detail or None

    def list_entries_for_day(
        self,
        employee_id: int | None,
        entry_date: date,
        project_id: int,
        task_id: int,
    ) -> list[dict]:
        resolved_employee_id = (
            int(employee_id) if employee_id is not None else self.get_current_employee_id()
        )
        domain = [
            ["employee_id", "=", resolved_employee_id],
            ["project_id", "=", project_id],
            ["task_id", "=", task_id],
            ["date", "=", entry_date.isoformat()],
        ]

        records = self.execute_kw(
            "account.analytic.line",
            "search_read",
            [domain],
            {
                "fields": ["id", "name", "date", "unit_amount", "employee_id", "project_id", "task_id", "user_id"],
                "order": "id asc",
            },
        )
        return list(records)

    def find_project_candidates(self, query: str, limit: int = 10) -> list[dict]:
        return self._search_name_candidates(
            model="project.project",
            query=query,
            fields=["id", "name"],
            limit=limit,
        )

    def find_employee_candidates(self, query: str, limit: int = 10) -> list[dict]:
        return self._search_name_candidates(
            model="hr.employee",
            query=query,
            fields=["id", "name"],
            limit=limit,
        )

    def find_task_candidates(
        self,
        query: str,
        project_id: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        base_domain = []
        if project_id is not None:
            base_domain.append(["project_id", "=", int(project_id)])

        return self._search_name_candidates(
            model="project.task",
            query=query,
            fields=["id", "name", "project_id"],
            base_domain=base_domain,
            limit=limit,
        )

    def list_tasks_for_project(self, project_id: int, limit: int = 20) -> list[dict]:
        domain = [["project_id", "=", int(project_id)]]
        records = self.execute_kw(
            "project.task",
            "search_read",
            [domain],
            {
                "fields": ["id", "name", "project_id"],
                "order": "id asc",
                "limit": limit,
            },
        )
        return list(records)

    def list_all_projects(self) -> list[dict]:
        return self._list_all_records(
            model="project.project",
            fields=["id", "name"],
        )

    def list_all_employees(self) -> list[dict]:
        return self._list_all_records(
            model="hr.employee",
            fields=["id", "name"],
        )

    def list_all_tasks(self, project_id: int | None = None) -> list[dict]:
        domain = []
        if project_id is not None:
            domain.append(["project_id", "=", int(project_id)])
        return self._list_all_records(
            model="project.task",
            fields=["id", "name", "project_id"],
            domain=domain,
        )

    def _search_name_candidates(
        self,
        model: str,
        query: str,
        fields: list[str],
        base_domain: list | None = None,
        limit: int = 10,
    ) -> list[dict]:
        variants = self._build_name_variants(query)
        if not variants:
            return []

        seen_ids: set[int] = set()
        merged: list[dict] = []

        for variant in variants:
            domain = list(base_domain or [])
            domain.append(["name", "ilike", variant])

            records = self.execute_kw(
                model,
                "search_read",
                [domain],
                {
                    "fields": fields,
                    "order": "id asc",
                    "limit": limit,
                },
            )

            for record in records:
                rec_id = int(record["id"])
                if rec_id in seen_ids:
                    continue
                seen_ids.add(rec_id)
                merged.append(record)
                if len(merged) >= limit:
                    return merged

        if not merged:
            # Fallback pool for fuzzy scoring when ilike variants miss due to typos.
            fallback_limit = max(limit * 6, 60)
            records = self.execute_kw(
                model,
                "search_read",
                [list(base_domain or [])],
                {
                    "fields": fields,
                    "order": "id desc",
                    "limit": fallback_limit,
                },
            )
            for record in records:
                rec_id = int(record["id"])
                if rec_id in seen_ids:
                    continue
                seen_ids.add(rec_id)
                merged.append(record)
                if len(merged) >= fallback_limit:
                    break

        return merged

    def _list_all_records(
        self,
        model: str,
        fields: list[str],
        domain: list | None = None,
        batch_size: int = 200,
    ) -> list[dict]:
        domain = list(domain or [])
        ids = self.execute_kw(
            model,
            "search",
            [domain],
            {
                "order": "id asc",
            },
        )
        if not ids:
            return []

        records: list[dict] = []
        total = len(ids)
        for idx in range(0, total, batch_size):
            chunk = ids[idx : idx + batch_size]
            batch = self.execute_kw(
                model,
                "read",
                [chunk],
                {
                    "fields": fields,
                },
            )
            records.extend(batch)

        return records

    @staticmethod
    def _build_name_variants(query: str) -> list[str]:
        cleaned = query.strip()
        if not cleaned:
            return []

        lowered = cleaned.lower()
        variants: list[str] = [cleaned]

        compact = re.sub(r"\s+", " ", lowered.replace("_", " ").replace("-", " ")).strip()
        if compact and compact not in variants:
            variants.append(compact)

        tokens = [token for token in re.split(r"\s+", compact) if token]
        for token in tokens:
            if len(token) >= 2 and token not in variants:
                variants.append(token)

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

        replaced = compact
        for word, digit in word_to_digit.items():
            replaced = re.sub(rf"\b{word}\b", digit, replaced)
        if replaced and replaced not in variants:
            variants.append(replaced)

        for word, digit in word_to_digit.items():
            if compact.startswith(word) and len(compact) > len(word):
                prefixed = f"{digit}{compact[len(word):]}".strip()
                if prefixed and prefixed not in variants:
                    variants.append(prefixed)

        alnum = re.sub(r"[^a-z0-9]", "", compact)
        if alnum and alnum not in variants:
            variants.append(alnum)

        return variants
