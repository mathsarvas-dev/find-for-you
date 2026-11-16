import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.deps import current_user_id
from api.main import app
from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, JobLabel, UserPreference, User

pytestmark = pytest.mark.db


@pytest.fixture
def env():
    with SessionLocal() as s:
        u = User(email=f"api-funnel-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
        c = Company(user_id=user_id, source="greenhouse", identifier=f"acme{user_id}",
                    name="Acme", collect_mode="funnel")
        s.add(c)
        s.commit()
        company_id = c.id
    app.dependency_overrides[current_user_id] = lambda: user_id
    yield user_id, company_id
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        s.execute(delete(JobLabel).where(JobLabel.user_id == user_id))
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(UserPreference).where(UserPreference.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def _job(user_id: int, company_id: int, n: int, intake: str, status: str = "open") -> int:
    with SessionLocal() as s:
        j = Job(
            user_id=user_id, company_id=company_id, external_id=f"e{n}",
            url=f"https://example.test/{n}", title=f"Vaga {n}", description="texto",
            raw={}, content_hash=f"h{n}", fingerprint=f"f{n}", intake=intake, status=status,
        )
        s.add(j)
        s.commit()
        return j.id


def test_funnel_counts(env, monkeypatch):
    user_id, company_id = env
    _job(user_id, company_id, 1, "full")
    _job(user_id, company_id, 2, "full")
    _job(user_id, company_id, 3, "stub")
    _job(user_id, company_id, 4, "gated")
    _job(user_id, company_id, 5, "full", status="closed")
    body = TestClient(app).get("/funnel").json()
    assert body["full"] == 2
    assert body["stub"] == 1
    assert body["gated"] == 1
    assert body["floor"] is None
    assert body["good_labels"] == 0


def test_put_floor_validates_range(env):
    client = TestClient(app)
    assert client.put("/funnel", json={"adherence_vec_floor": 1.5}).status_code == 422
    assert client.put("/funnel", json={"adherence_vec_floor": -0.1}).status_code == 422
    # Python's json cannot send NaN, so the raw body is the one a client could send
    nan = client.put("/funnel", content=b'{"adherence_vec_floor": NaN}',
                     headers={"content-type": "application/json"})
    assert nan.status_code == 422


def test_put_floor_moves_jobs_and_null_restores(env):
    user_id, company_id = env
    gated_id = _job(user_id, company_id, 1, "gated")
    client = TestClient(app)
    # no search profile: nothing can be scored, so nothing moves
    resp = client.put("/funnel", json={"adherence_vec_floor": 0.5})
    assert resp.status_code == 200
    assert resp.json()["floor"] == 0.5
    assert client.get("/funnel").json()["floor"] == 0.5

    resp = client.put("/funnel", json={"adherence_vec_floor": None})
    assert resp.status_code == 200
    assert resp.json()["floor"] is None
    with SessionLocal() as s:
        assert s.get(Job, gated_id).intake == "full"
