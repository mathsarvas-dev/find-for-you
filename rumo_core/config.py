from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    # background LLM work (job field fill) uses its own model, so its token quota
    # (Groq counts tokens per day per model) never starves resume import,
    # interview and matching, which keep `groq_model`. Empty = same as groq_model.
    groq_fill_model: str = "openai/gpt-oss-20b"
    # Used ONLY by non-critical background work (the job-field fill) when its model has no
    # quota left or disappeared (404 model_not_found). Empty = no fallback.
    groq_fallback_model: str = "llama-3.1-8b-instant"
    groq_max_per_minute: int = Field(25, gt=0)
    default_user_id: int = 1
    upload_max_bytes: int = Field(5_000_000, gt=0)
    extract_max_chars: int = Field(20_000, gt=0)
    embed_batch_size: int = Field(8, gt=0)
    # Jobs embedded per worker tick (~0.4 texts/s on CPU): small so a job
    # backlog never delays interview turns by more than a few seconds.
    job_embed_batch_size: int = Field(2, gt=0)
    collect_interval_hours: int = Field(6, gt=0)
    collect_delay_seconds: float = Field(1.0, ge=0)
    collect_max_companies_per_run: int = Field(3, gt=0)
    # A collect tick stops claiming companies after this many seconds.
    collect_max_seconds: float = Field(60, gt=0)
    fetch_timeout_seconds: float = Field(20, gt=0)
    fetch_max_bytes: int = Field(20_000_000, gt=0)
    llm_fill_batch: int = Field(3, gt=0)
    match_batch: int = Field(3, gt=0)
    match_top_n: int = Field(30, gt=0)
    match_weight_profile: float = Field(1.0, ge=0)
    match_weight_query: float = Field(0.5, ge=0)
    match_max_attempts: int = Field(3, gt=0)
    # Max matches scored by the LLM in any rolling 24 h (all users): protects
    # the Groq daily budget shared with the interview.
    match_daily_limit: int = Field(20, gt=0)
    # Seconds one scoring call may take (429 retries included) before the
    # tick gives up and pauses: the worker loop is sequential.
    match_call_timeout: float = Field(25, gt=0)
    # Automatic company discovery.
    discovery_batch: int = Field(5, gt=0)
    discovery_max_seconds: float = Field(20, gt=0)
    discovery_max_auto_companies: int = Field(100, gt=0)
    discovery_max_attempts: int = Field(3, gt=0)
    # Job sources (marco 3b).
    fetch_page_max_bytes: int = Field(5_000_000, gt=0)
    fetch_host_min_interval_seconds: float = Field(2.0, ge=0)
    fetch_host_daily_cap: int = Field(500, gt=0)
    site_max_links: int = Field(200, gt=0)
    site_max_new_pages: int = Field(25, gt=0)
    feed_job_ttl_days: int = Field(7, gt=0)
    collect_feeds_max_per_run: int = Field(2, gt=0)
    # Aggregator keys: .env only; never stored in the database, logged or returned.
    adzuna_app_id: str = Field("", repr=False)
    adzuna_app_key: str = Field("", repr=False)
    jooble_key: str = Field("", repr=False)
    careerjet_key: str = Field("", repr=False)
    # Candidatura (marco 5).
    application_daily_limit: int = Field(5, gt=0)
    application_max_attempts: int = Field(3, gt=0)
    application_call_timeout: float = Field(25, gt=0)
    greenhouse_questions_timeout: float = Field(10, gt=0)
    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
