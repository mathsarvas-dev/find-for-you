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
    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
