from __future__ import annotations

from datetime import date
import json
import random
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings
from app.models.schemas import TimesheetCreate, TimesheetUpdate


class OdooClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._uid: int | None = None
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
            raise RuntimeError("Odoo authentication failed. Check credentials.")
        self._uid = int(uid)
        return self._uid

    def _ensure_uid(self) -> int:
        if self._uid is None:
            return self.authenticate()
        return self._uid

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
        values = {
            "name": payload.description,
            "date": payload.date.isoformat(),
            "unit_amount": payload.hours,
            "employee_id": payload.employee_id,
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
        employee_id: int,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        domain: list[list[str | int]] = [["employee_id", "=", employee_id]]

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
                raise RuntimeError(f"Odoo JSON-RPC error: {message} | {data}")
            raise RuntimeError(f"Odoo JSON-RPC error: {message}")

        if "result" not in response_json:
            raise RuntimeError("Odoo JSON-RPC response missing 'result'")

        return response_json["result"]

    def list_entries_for_day(
        self,
        employee_id: int,
        entry_date: date,
        project_id: int,
        task_id: int,
    ) -> list[dict]:
        domain = [
            ["employee_id", "=", employee_id],
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
