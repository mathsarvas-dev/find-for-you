import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import rumo_core.sources.feeds as feeds_registry
from api.deps import current_user_id
from api.main import app
from rumo_core.db import SessionLocal
from rumo_core.models import Company, DiscoveryCandidate, Job, JobFeed, User
from rumo_core.sources.portals.gate import MSG_BLOCKED, MSG_CONDITIONAL_OFF

pytestmark = pytest.mark.db


def _new_user() -> int:
    with SessionLocal() as s:
        u = User(email=f"api-feeds-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        return u.id


def _drop_user(user_id: int) -> None:
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == user_id))
        s.execute(delete(JobFeed).where(JobFeed.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


@pytest.fixture
def uid():
    user_id = _new_user()
    app.dependency_overrides[current_user_id] = lambda: user_id
    yield user_id
    app.dependency_overrides.clear()
    _drop_user(user_id)


@pytest.fixture
def client(uid):
    return TestClient(app)


class _FakeFeed:
    def __init__(self, configured: bool) -> None:
        self._configured = configured

    def key_configured(self, settings) -> bool:
        return self._configured


def _names_with(*extra: str):
    base = feeds_registry.feed_names()
    return lambda: sorted(set(base) | set(extra))


def test_create_list_delete_feed(client):
    created = client.post("/feeds", json={
        "source": "remotive", "params": {"keywords": ["python"]},
    })
    assert created.status_code == 201
    body = created.json()
    assert body["params"] == {"keywords": ["python"], "country": None}
    assert body["attribution"] == {"label": "Vagas via Remotive", "url": "https://remotive.com"}
    assert body["next_run_at"] is not None
    assert body["active"] is True

    listing = client.get("/feeds").json()
    assert [f["id"] for f in listing["feeds"]] == [body["id"]]
    remotive = next(s for s in listing["sources"] if s["source"] == "remotive")
    assert remotive["gate_allowed"] is True
    assert remotive["gate_status"] == "allowed"
    assert remotive["needs_key"] is False
    assert remotive["label"] == "Remotive"

    assert client.delete(f"/feeds/{body['id']}").status_code == 204
    assert client.get("/feeds").json()["feeds"] == []


def test_feed_errors(client, monkeypatch):
    assert client.post("/feeds", json={"source": "nope", "params": {}}).json() == {
        "detail": "Fonte desconhecida."
    }
    assert client.post("/feeds", json={
        "source": "remotive", "params": {"keywords": [f"k{i}" for i in range(6)]},
    }).status_code == 422

    first = {"source": "remotive", "params": {"keywords": ["python"]}}
    assert client.post("/feeds", json=first).status_code == 201
    dup = client.post("/feeds", json=first)
    assert dup.status_code == 409
    assert dup.json()["detail"] == "Esse feed já existe."

    for i in range(19):
        ok = client.post("/feeds", json={"source": "remotive", "params": {"keywords": [f"w{i}"]}})
        assert ok.status_code == 201
    over = client.post("/feeds", json={"source": "remotive", "params": {"keywords": ["extra"]}})
    assert over.status_code == 422
    assert over.json()["detail"] == "Limite de 20 feeds atingido."


def test_blocked_and_conditional_feeds_follow_gate(client, monkeypatch):
    monkeypatch.setattr("api.feeds.feed_names", _names_with("weworkremotely", "bundesagentur"))
    blocked = client.post("/feeds", json={"source": "weworkremotely", "params": {}})
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == MSG_BLOCKED
    off = client.post("/feeds", json={"source": "bundesagentur", "params": {}})
    assert off.status_code == 409
    assert off.json()["detail"] == MSG_CONDITIONAL_OFF


def test_keyed_feed_without_key_is_422(client, monkeypatch):
    monkeypatch.setattr("api.feeds.feed_names", _names_with("adzuna"))
    monkeypatch.setattr("api.feeds.feed_needs_key", lambda name: name == "adzuna")
    monkeypatch.setattr("api.feeds.get_feed", lambda name: _FakeFeed(configured=False))
    resp = client.post("/feeds", json={"source": "adzuna", "params": {}})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Chave da API não configurada no .env."


def test_feed_sources_never_return_key_values(client, monkeypatch):
    monkeypatch.setattr("api.feeds.feed_names", _names_with("adzuna"))
    monkeypatch.setattr("api.feeds.feed_needs_key", lambda name: name == "adzuna")
    monkeypatch.setattr("api.feeds.get_feed", lambda name: _FakeFeed(configured=True))
    text = client.get("/feeds").text
    adzuna = next(s for s in client.get("/feeds").json()["sources"] if s["source"] == "adzuna")
    assert adzuna["needs_key"] is True
    assert adzuna["key_configured"] is True
    assert "segredo" not in text


def test_feeds_are_user_scoped(client, uid):
    created = client.post("/feeds", json={"source": "remotive", "params": {}}).json()
    other = _new_user()
    try:
        app.dependency_overrides[current_user_id] = lambda: other
        assert client.get("/feeds").json()["feeds"] == []
        assert client.delete(f"/feeds/{created['id']}").status_code == 404
    finally:
        app.dependency_overrides[current_user_id] = lambda: uid
        _drop_user(other)
    assert len(client.get("/feeds").json()["feeds"]) == 1
