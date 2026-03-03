from app.clients.odoo import OdooClient
from app.core.config import get_settings
from app.models.schemas import TimesheetFillWeekRequest
from app.services.timesheet_service import OdooTimesheetService
from app.workers.celery_app import celery_app


@celery_app.task(name="timesheet.fill_week")
def fill_week_task(payload: dict) -> dict:
    settings = get_settings()
    service = OdooTimesheetService(OdooClient(settings))
    request = TimesheetFillWeekRequest.model_validate(payload)
    response = service.fill_week(request)
    return response.model_dump(mode="json")
