from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    google_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_requirement_chars: int = Field(default=10_000, gt=0, le=10_000)
    max_upload_bytes: int = Field(default=10_485_760, gt=0, le=25_000_000)
    gemini_requests_per_minute: int = Field(default=0, ge=0)
    gemini_tokens_per_minute: int = Field(default=0, ge=0)
    gemini_requests_per_day: int = Field(default=0, ge=0)
    gemini_max_concurrency: int = Field(default=3, ge=1, le=100)
    gemini_rate_limit_max_wait_seconds: float = Field(default=30, ge=0, le=3600)
    gemini_max_retries: int = Field(default=2, ge=0, le=5)
    gemini_retry_base_seconds: float = Field(default=0.5, gt=0, le=60)
    gemini_retry_max_seconds: float = Field(default=15, gt=0, le=300)
    gemini_timeout_seconds: float = Field(default=45, gt=0, le=600)
    gemini_max_output_tokens: int = Field(default=8192, gt=0, le=65_536)
    generation_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    generation_cache_max_entries: int = Field(default=256, ge=0, le=10_000)

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
