from functools import lru_cache

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "odoo-timesheet-automation"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    timezone: str = "UTC"

    odoo_url: AnyHttpUrl
    odoo_db: str
    odoo_username: str
    odoo_password: str
    transcription_url: AnyHttpUrl = "https://aqs-shispare-transcript-api.hf.space/voice"

    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    chat_session_redis_url: str = "redis://redis:6379/2"
    chat_session_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    llm_provider: str = "groq"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    default_daily_hours: float = Field(default=8.0, gt=0, le=24)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"gemini", "groq"}:
            raise ValueError("llm_provider must be either 'gemini' or 'groq'")
        return normalized

    @property
    def odoo_base_url(self) -> str:
        return str(self.odoo_url).rstrip("/")

    @property
    def gemini_generate_content_url(self) -> str:
        return (
            f"{self.gemini_api_base.rstrip('/')}/models/"
            f"{self.gemini_model}:generateContent"
        )

    @property
    def groq_chat_completions_url(self) -> str:
        return f"{self.groq_api_base.rstrip('/')}/chat/completions"


@lru_cache
def get_settings() -> Settings:
    return Settings()
