from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_per_minute: int = 25
    default_user_id: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
