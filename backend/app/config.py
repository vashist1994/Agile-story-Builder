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
