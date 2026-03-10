from fastapi import FastAPI

from app.api.automation import router as automation_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.timesheets import router as timesheet_router
from app.api.voice import router as voice_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="Odoo Timesheet Automation Service",
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(timesheet_router)
app.include_router(automation_router)
app.include_router(chat_router)
app.include_router(voice_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "running",
    }
