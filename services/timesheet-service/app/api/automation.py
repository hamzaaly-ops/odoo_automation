from fastapi import APIRouter, Depends, HTTPException

from app.api.timesheets import get_timesheet_service
from app.models.schemas import (
    AutomationTaskQueued,
    AutomationTaskStatus,
    TimesheetFillWeekRequest,
    TimesheetFillWeekResponse,
)
from app.services.timesheet_service import OdooTimesheetService
from app.workers.celery_app import celery_app
from app.workers.tasks import fill_week_task

router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


@router.post("/fill-week", response_model=AutomationTaskQueued)
def queue_fill_week(payload: TimesheetFillWeekRequest) -> AutomationTaskQueued:
    task = fill_week_task.delay(payload.model_dump(mode="json"))
    return AutomationTaskQueued(task_id=task.id, status=task.status)


@router.post("/fill-week/sync", response_model=TimesheetFillWeekResponse)
def fill_week_sync(
    payload: TimesheetFillWeekRequest,
    service: OdooTimesheetService = Depends(get_timesheet_service),
) -> TimesheetFillWeekResponse:
    try:
        return service.fill_week(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{task_id}", response_model=AutomationTaskStatus)
def get_job_status(task_id: str) -> AutomationTaskStatus:
    result = celery_app.AsyncResult(task_id)

    output = None
    if result.ready() and isinstance(result.result, dict):
        output = result.result

    if result.failed():
        output = {"error": str(result.result)}

    return AutomationTaskStatus(task_id=task_id, status=result.status, result=output)
