import pytest
from pydantic import ValidationError

from rumo_core.config import Settings, get_settings


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/rumo")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    get_settings.cache_clear()
    s = get_settings()
    assert s.groq_model == "llama-3.3-70b-versatile"
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
