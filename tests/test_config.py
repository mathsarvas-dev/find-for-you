import pytest
from pydantic import ValidationError

from rumo_core.config import Settings, get_settings


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/rumo")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    get_settings.cache_clear()
    s = get_settings()
    assert s.groq_model == "openai/gpt-oss-120b"
    assert s.groq_max_per_minute == 25
    assert s.default_user_id == 1
    get_settings.cache_clear()


def test_settings_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_max_per_minute_must_be_positive(monkeypatch):
    monkeypatch.setenv("GROQ_MAX_PER_MINUTE", "0")
    with pytest.raises(ValidationError):
        Settings(database_url="x", _env_file=None)


def test_collection_settings_defaults():
    s = Settings(database_url="x", _env_file=None)
    assert s.collect_interval_hours == 6
    assert s.collect_delay_seconds == 1.0
    assert s.fetch_timeout_seconds == 20
    assert s.fetch_max_bytes == 20_000_000
    assert s.llm_fill_batch == 3


@pytest.mark.parametrize(
    "var,value",
    [
        ("COLLECT_INTERVAL_HOURS", "0"),
        ("COLLECT_DELAY_SECONDS", "-1"),
        ("FETCH_TIMEOUT_SECONDS", "0"),
        ("FETCH_MAX_BYTES", "0"),
        ("LLM_FILL_BATCH", "0"),
    ],
)
def test_collection_settings_constraints(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings(database_url="x", _env_file=None)


def test_collect_delay_may_be_zero(monkeypatch):
    monkeypatch.setenv("COLLECT_DELAY_SECONDS", "0")
    assert Settings(database_url="x", _env_file=None).collect_delay_seconds == 0


def test_match_weight_query_default():
    s = Settings(_env_file=None, database_url="x")
    assert s.match_weight_query == 0.5


def test_groq_fill_model_default_is_separate_from_main_model():
    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")
    assert s.groq_fill_model == "openai/gpt-oss-20b"
    assert s.groq_fill_model != s.groq_model


def test_build_llm_model_override_keeps_main_model_untouched():
    from worker.main import build_llm

    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db", groq_api_key="k")
    main, fill = build_llm(s), build_llm(s, model=s.groq_fill_model)
    assert main._settings.groq_model == "openai/gpt-oss-120b"
    assert fill._settings.groq_model == "openai/gpt-oss-20b"
    assert s.groq_model == "openai/gpt-oss-120b"
    assert build_llm(Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")) is None
