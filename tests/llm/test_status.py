import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app, current_user_id
from rumo_core.config import Settings
from rumo_core.db import SessionLocal
from rumo_core.llm.status import llm_status
from rumo_core.models import LlmModelStatus, ProfileImport, User

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
MODELS = ("test/main", "test/fill", "test/reserve")

pytestmark = pytest.mark.db


def _settings(**kw):
    return Settings(
        _env_file=None, database_url="x", groq_model="test/main",
        groq_fill_model="test/fill", groq_fallback_model="test/reserve", **kw,
    )


@pytest.fixture
def clean():
    def wipe():
        with SessionLocal() as s:
            s.query(LlmModelStatus).filter(LlmModelStatus.model.in_(MODELS)).delete()
            s.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"llm-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        uid = u.id
    yield uid
    with SessionLocal() as s:
        s.query(ProfileImport).filter(ProfileImport.user_id == uid).delete()
        s.query(User).filter(User.id == uid).delete()
        s.commit()


def test_lists_each_configured_model_with_roles_even_without_readings(clean, uid):
    with SessionLocal() as s:
        out = llm_status(s, _settings(), uid, now=NOW)
    by = {m["model"]: m for m in out["models"]}
    assert set(by) == set(MODELS)
    assert by["test/main"]["roles"] == ["main"]
    assert by["test/fill"]["roles"] == ["fill"]
    assert by["test/reserve"]["roles"] == ["fallback"]
    assert by["test/main"]["exists"] is None and by["test/main"]["quota"] is None


def test_one_model_in_several_roles_appears_once(clean, uid):
    s2 = Settings(_env_file=None, database_url="x", groq_model="test/main",
                  groq_fill_model="test/main", groq_fallback_model="")
    with SessionLocal() as s:
        out = llm_status(s, s2, uid, now=NOW)
    assert [m["model"] for m in out["models"]] == ["test/main"]
    assert out["models"][0]["roles"] == ["main", "fill"]


def test_reports_quota_exhaustion_missing_and_next_try(clean, uid):
    with SessionLocal() as s:
        s.add(LlmModelStatus(
            model="test/main", exists=True, observed_at=NOW,
            limit_tokens=8000, remaining_tokens=0, reset_tokens_at=NOW + timedelta(seconds=20),
            limit_requests=1000, remaining_requests=900,
            reset_requests_at=NOW + timedelta(hours=3), next_try_at=NOW + timedelta(seconds=30),
        ))
        s.add(LlmModelStatus(model="test/fill", exists=False, missing_since=NOW - timedelta(hours=1)))
        s.commit()
        out = llm_status(s, _settings(), uid, now=NOW)
    by = {m["model"]: m for m in out["models"]}
    main = by["test/main"]
    assert main["exhausted"] is True
    assert main["quota"]["tokens"] == {
        "limit": 8000, "remaining": 0, "reset_at": (NOW + timedelta(seconds=20)).isoformat(),
    }
    assert main["quota"]["requests"]["remaining"] == 900
    assert main["next_try_at"] == (NOW + timedelta(seconds=30)).isoformat()
    assert by["test/fill"]["exists"] is False and by["test/fill"]["missing_since"]
    assert by["test/reserve"]["exhausted"] is False


def test_past_next_try_and_reset_are_dropped(clean, uid):
    with SessionLocal() as s:
        s.add(LlmModelStatus(
            model="test/main", exists=True, observed_at=NOW - timedelta(hours=2),
            limit_tokens=8000, remaining_tokens=0, reset_tokens_at=NOW - timedelta(hours=1),
            next_try_at=NOW - timedelta(minutes=1),
        ))
        s.commit()
        out = llm_status(s, _settings(), uid, now=NOW)
    main = {m["model"]: m for m in out["models"]}["test/main"]
    assert main["exhausted"] is False and main["next_try_at"] is None


def test_queue_lists_resume_imports_waiting_for_quota(clean, uid):
    with SessionLocal() as s:
        for i, mins in enumerate((9, 4)):
            s.add(ProfileImport(
                user_id=uid, filename=f"cv{i}.pdf", content_type="application/pdf",
                status="pending", retry_at=NOW + timedelta(minutes=mins),
            ))
        s.add(ProfileImport(  # waiting but not rate limited: not in the retry queue
            user_id=uid, filename="n.pdf", content_type="application/pdf", status="pending"))
        s.commit()
        out = llm_status(s, _settings(), uid, now=NOW)
    assert out["queue"] == [{
        "kind": "resume_import", "count": 2,
        "next_try_at": (NOW + timedelta(minutes=4)).isoformat(),
    }]


def test_route_returns_status(clean, uid):
    app.dependency_overrides[current_user_id] = lambda: uid
    try:
        r = TestClient(app).get("/llm/status")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"models", "queue"}
    assert body["models"] and {"model", "roles", "exists", "quota", "exhausted"} <= set(body["models"][0])
