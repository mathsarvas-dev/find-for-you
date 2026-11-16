import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.deps import current_user_id
from api.main import app
from rumo_core.db import SessionLocal
from rumo_core.models import PortalState, User
from rumo_core.sources.portals.state import mark_blocked_by_site
from datetime import UTC, datetime

pytestmark = pytest.mark.db


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"api-portals-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    app.dependency_overrides[current_user_id] = lambda: user_id
    yield user_id
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        s.execute(delete(PortalState).where(PortalState.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


@pytest.fixture
def client(uid):
    return TestClient(app)


def _by_id(client) -> dict:
    return {p["id"]: p for p in client.get("/portals").json()}


def test_list_portals_from_policy(client):
    portals = _by_id(client)
    assert portals["indeed"]["status"] == "blocked"
    assert portals["indeed"]["can_toggle"] is False
    assert portals["vagas"]["status"] == "conditional"
    assert portals["vagas"]["can_toggle"] is True
    assert portals["vagas"]["enabled"] is False
    assert portals["vagas"]["blocked_by_site"] is False
    assert portals["vagas"]["blocked_at"] is None
    assert portals["vagas"]["checked_at"] == "2026-11-14"
    assert isinstance(portals["vagas"]["evidence_urls"], list)


def test_toggle_conditional_only(client):
    assert client.put("/portals/nope", json={"enabled": True}).status_code == 404
    blocked = client.put("/portals/indeed", json={"enabled": True})
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Só portais condicionais podem ser ligados ou desligados."
    on = client.put("/portals/vagas", json={"enabled": True})
    assert on.status_code == 200
    assert on.json()["enabled"] is True
    assert _by_id(client)["vagas"]["enabled"] is True


def test_toggle_keeps_blocked_by_site(client, uid):
    with SessionLocal() as s:
        mark_blocked_by_site(s, uid, "vagas", datetime(2026, 11, 15, tzinfo=UTC))
        s.commit()
    row = _by_id(client)["vagas"]
    assert row["blocked_by_site"] is True
    assert row["blocked_at"] is not None
    client.put("/portals/vagas", json={"enabled": True})
    assert _by_id(client)["vagas"]["blocked_by_site"] is True
