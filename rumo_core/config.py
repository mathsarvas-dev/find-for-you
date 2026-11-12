from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_per_minute: int = Field(25, gt=0)
    default_user_id: int = 1
    upload_max_bytes: int = Field(5_000_000, gt=0)
    extract_max_chars: int = Field(20_000, gt=0)
    embed_batch_size: int = Field(8, gt=0)
    collect_interval_hours: int = Field(6, gt=0)
    collect_delay_seconds: float = Field(1.0, ge=0)
    fetch_timeout_seconds: float = Field(20, gt=0)
    fetch_max_bytes: int = Field(20_000_000, gt=0)
    llm_fill_batch: int = Field(5, gt=0)
    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
