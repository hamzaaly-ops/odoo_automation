from __future__ import annotations

from datetime import date as dt_date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TimesheetCreate(BaseModel):
    description: str = Field(min_length=1, max_length=512)
    date: dt_date
    hours: float = Field(gt=0, le=24)
    employee_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    task_id: int = Field(gt=0)
    user_id: int | None = Field(default=None, gt=0)


class TimesheetUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=512)
    date: dt_date | None = None
    hours: float | None = Field(default=None, gt=0, le=24)
    employee_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    task_id: int | None = Field(default=None, gt=0)
    user_id: int | None = Field(default=None, gt=0)


class TimesheetRead(BaseModel):
    id: int
    description: str
    date: dt_date
    hours: float
    employee_id: int
    project_id: int
    task_id: int
    user_id: int | None = None


class TimesheetListResponse(BaseModel):
    entries: list[TimesheetRead]


class TimesheetFillWeekRequest(BaseModel):
    employee_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    task_id: int = Field(gt=0)
    week_start: dt_date
    daily_hours: float = Field(default=8.0, gt=0, le=24)
    description_template: str = Field(
        default="Automated timesheet entry for {date}",
        min_length=1,
        max_length=512,
    )
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    overwrite_existing: bool = False
    user_id: int | None = Field(default=None, gt=0)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("weekdays cannot be empty")
        unique_days = sorted(set(value))
        if any(day < 0 or day > 6 for day in unique_days):
            raise ValueError("weekdays must be integers between 0 and 6")
        return unique_days


class TimesheetFillWeekResponse(BaseModel):
    created_entry_ids: list[int] = Field(default_factory=list)
    updated_entry_ids: list[int] = Field(default_factory=list)
    skipped_dates: list[dt_date] = Field(default_factory=list)


class AutomationTaskQueued(BaseModel):
    task_id: str
    status: str


class AutomationTaskStatus(BaseModel):
    task_id: str
    status: str
    result: dict | None = None


class ChatQueryRequest(BaseModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


class ChatQueryResponse(BaseModel):
    session_id: str
    status: Literal["needs_clarification", "completed", "message", "error"]
    assistant_message: str
    action: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    result: dict | list[dict] | None = None


class LlmActionExtraction(BaseModel):
    action: Literal[
        "none",
        "create_timesheet",
        "update_timesheet",
        "list_timesheets",
        "fill_week",
    ] = "none"
    fields: dict[str, Any] = Field(default_factory=dict)
    user_message: str | None = None
