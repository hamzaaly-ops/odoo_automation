from __future__ import annotations

from datetime import timedelta

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
        entry_id = self._client.create_timesheet(payload)
        record = self._client.get_timesheet(entry_id)

        if record is None:
            raise RuntimeError(f"Created timesheet {entry_id} but failed to fetch it")

        return self._map_record(record)

    def update_timesheet(self, entry_id: int, payload: TimesheetUpdate) -> TimesheetRead:
        self._client.update_timesheet(entry_id, payload)
        record = self._client.get_timesheet(entry_id)

        if record is None:
            raise RuntimeError(f"Timesheet {entry_id} not found after update")

        return self._map_record(record)

    def list_timesheets(
        self,
        employee_id: int,
        date_from=None,
        date_to=None,
    ) -> list[TimesheetRead]:
        records = self._client.list_timesheets(employee_id, date_from, date_to)
        return [self._map_record(record) for record in records]

    def fill_week(self, payload: TimesheetFillWeekRequest) -> TimesheetFillWeekResponse:
        created_entry_ids: list[int] = []
        updated_entry_ids: list[int] = []
        skipped_dates = []

        for weekday in payload.weekdays:
            entry_date = payload.week_start + timedelta(days=weekday)
            existing_entries = self._client.list_entries_for_day(
                payload.employee_id,
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
                        employee_id=payload.employee_id,
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
                    employee_id=payload.employee_id,
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
