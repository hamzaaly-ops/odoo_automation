from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.clients.odoo import OdooClient
from app.core.config import get_settings
from app.models.schemas import (
    TimesheetCreate,
    TimesheetListResponse,
    TimesheetRead,
    TimesheetUpdate,
)
from app.services.timesheet_service import OdooTimesheetService

router = APIRouter(prefix="/api/v1/timesheets", tags=["timesheets"])


def get_timesheet_service() -> OdooTimesheetService:
    settings = get_settings()
    return OdooTimesheetService(OdooClient(settings))


@router.post("", response_model=TimesheetRead)
def create_timesheet(
    payload: TimesheetCreate,
    service: OdooTimesheetService = Depends(get_timesheet_service),
) -> TimesheetRead:
    try:
        return service.create_timesheet(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{entry_id}", response_model=TimesheetRead)
def update_timesheet(
    entry_id: int,
    payload: TimesheetUpdate,
    service: OdooTimesheetService = Depends(get_timesheet_service),
) -> TimesheetRead:
    try:
        return service.update_timesheet(entry_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=TimesheetListResponse)
def list_timesheets(
    employee_id: int | None = Query(default=None, gt=0),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    service: OdooTimesheetService = Depends(get_timesheet_service),
) -> TimesheetListResponse:
    try:
        entries = service.list_timesheets(employee_id, date_from, date_to)
        return TimesheetListResponse(entries=entries)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
