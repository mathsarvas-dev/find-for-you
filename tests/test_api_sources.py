import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import api.sources as api_sources
from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, User
from rumo_core.sources import (
    GreenhouseSource,
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeSource:
    name = "greenhouse"

    def __init__(self, error=None, delay=0.0):
        self.error = error
        self.delay = delay
        self.calls: list[str] = []
        self._real = GreenhouseSource.normalize_identifier

    def normalize_identifier(self, text):
        return self._real(self, text)

    async def fetch_jobs(self, identifier):
        self.calls.append(identifier)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return []

    async def aclose(self):
        raise AssertionError("API must not close the shared source")


@pytest.fixture
def uids():
    with SessionLocal() as s:
        tag = uuid.uuid4().hex
        users = [User(email=f"api-src-{tag}-{i}@example.test") for i in (1, 2)]
        s.add_all(users)
        s.commit()
        ids = [u.id for u in users]
    yield ids
    with SessionLocal() as s:
        s.execute(delete(Company).where(Company.user_id.in_(ids)))  # jobs cascade
        s.execute(delete(User).where(User.id.in_(ids)))
        s.commit()


@pytest.fixture
def fake():
    return FakeSource()


@pytest.fixture
def client(uids, fake):
    app.dependency_overrides[current_user_id] = lambda: uids[0]
    app.dependency_overrides[api_sources.source_getter] = lambda: (lambda name: fake)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _company(uid, identifier="acme", name="Acme", **kw):
    with SessionLocal() as s:
        c = Company(user_id=uid, source="greenhouse", identifier=identifier, name=name, **kw)
        s.add(c)
        s.commit()
        return c.id


def _job(uid, cid, ext, title="Dev", **kw):
    with SessionLocal() as s:
        j = Job(user_id=uid, company_id=cid, external_id=ext, url=f"https://x/{ext}",
                title=title, description=kw.pop("description", "Texto limpo"),
                raw={"big": "blob"}, content_hash="h" + ext, fingerprint="f" + ext, **kw)
        s.add(j)
        s.commit()
        return j.id


def _count(uid):
    with SessionLocal() as s:
        return len(s.scalars(select(Company.id).where(Company.user_id == uid)).all())


@pytest.mark.db
def test_add_company_normalizes_url_and_validates(client, uids, fake):
    r = client.post("/companies", json={
        "source": "greenhouse", "identifier": "https://boards.greenhouse.io/Acme", "country": "br"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["identifier"] == "acme"
    assert body["name"] == "acme"
    assert body["country"] == "BR"
    assert body["active"] is True
    assert body["next_run_at"] is not None
    assert fake.calls == ["acme"]


@pytest.mark.db
def test_add_company_invalid_identifier_422(client, fake):
    r = client.post("/companies", json={"source": "greenhouse", "identifier": "a b"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Token do quadro inválido."
    assert fake.calls == []


@pytest.mark.db
def test_add_company_invalid_country_422(client):
    r = client.post("/companies", json={"source": "greenhouse", "identifier": "acme",
                                        "country": "BRA"})
    assert r.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("error,code,msg", [
    (SourceNotFound("secret"), 422, "Quadro não encontrado no Greenhouse."),
    (SourceUnavailable("secret"), 503, "Fonte indisponível no momento."),
    (SourceBadResponse("secret"), 502, "Resposta inesperada da fonte."),
])
def test_add_company_source_errors_nothing_stored(client, uids, fake, error, code, msg):
    fake.error = error
    r = client.post("/companies", json={"source": "greenhouse", "identifier": "acme"})
    assert r.status_code == code
    assert r.json()["detail"] == msg
    assert _count(uids[0]) == 0


@pytest.mark.db
def test_add_company_timeout_503_nothing_stored(client, uids, fake, monkeypatch):
    fake.delay = 1.0
    monkeypatch.setattr(api_sources, "FETCH_TIMEOUT_SECONDS", 0.05)
    r = client.post("/companies", json={"source": "greenhouse", "identifier": "acme"})
    assert r.status_code == 503
    assert r.json()["detail"] == "A fonte demorou demais para responder."
    assert _count(uids[0]) == 0


@pytest.mark.db
def test_add_company_duplicate_409(client, uids):
    body = {"source": "greenhouse", "identifier": "acme"}
    assert client.post("/companies", json=body).status_code == 201
    r = client.post("/companies", json=body)
    assert r.status_code == 409
    assert r.json()["detail"] == "Essa empresa já está cadastrada."
    assert _count(uids[0]) == 1


@pytest.mark.db
def test_add_company_integrity_race_409(client, uids):
    real = Session.commit

    def boom(self):
        raise IntegrityError("insert", {}, Exception("dup"))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Session, "commit", boom)
        r = client.post("/companies", json={"source": "greenhouse", "identifier": "acme"})
    assert Session.commit is real
    assert r.status_code == 409
    assert _count(uids[0]) == 0


@pytest.mark.db
def test_unknown_source_422(client):
    r = client.post("/companies", json={"source": "lever", "identifier": "acme"})
    assert r.status_code == 422


@pytest.mark.db
def test_list_companies_health_fields(client, uids):
    now = datetime.now(timezone.utc)
    _company(uids[0], "zeta", "Zeta")
    _company(uids[0], "acme", "Acme", last_run_at=now, last_success_at=now, next_run_at=now,
             last_error="boom", consecutive_failures=2, open_jobs=3, country="BR")
    _company(uids[1], "other", "Other")
    r = client.get("/companies")
    assert r.status_code == 200
    items = r.json()
    assert [c["name"] for c in items] == ["Acme", "Zeta"]
    assert set(items[0]) == {
        "id", "source", "identifier", "name", "country", "active", "last_run_at",
        "last_success_at", "next_run_at", "last_error", "consecutive_failures", "open_jobs"}
    assert items[0]["consecutive_failures"] == 2 and items[0]["open_jobs"] == 3
    assert items[0]["last_error"] == "boom"


@pytest.mark.db
def test_patch_toggle_active(client, uids):
    cid = _company(uids[0], active=False, next_run_at=None)
    r = client.patch(f"/companies/{cid}", json={"active": True, "name": " Novo ", "country": "us"})
    assert r.status_code == 200
    b = r.json()
    assert b["active"] is True and b["name"] == "Novo" and b["country"] == "US"
    assert b["next_run_at"] is not None
    r = client.patch(f"/companies/{cid}", json={"active": False})
    assert r.json()["active"] is False


@pytest.mark.db
@pytest.mark.parametrize("payload", [{"name": ""}, {"name": "   "}, {"name": "x" * 201},
                                     {"country": "BRA"}])
def test_patch_validation_422(client, uids, payload):
    cid = _company(uids[0])
    assert client.patch(f"/companies/{cid}", json=payload).status_code == 422


@pytest.mark.db
def test_collect_now_sets_next_run(client, uids):
    cid = _company(uids[0], next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
                   consecutive_failures=3)
    r = client.post(f"/companies/{cid}/collect-now")
    assert r.status_code == 200
    b = r.json()
    assert datetime.fromisoformat(b["next_run_at"]) <= datetime.now(timezone.utc)
    assert b["consecutive_failures"] == 3


@pytest.mark.db
def test_collect_now_inactive_409(client, uids):
    cid = _company(uids[0], active=False)
    assert client.post(f"/companies/{cid}/collect-now").status_code == 409


@pytest.mark.db
def test_cross_user_404(client, uids):
    cid = _company(uids[1], "other", "Other")
    jid = _job(uids[1], cid, "1")
    assert client.patch(f"/companies/{cid}", json={"active": False}).status_code == 404
    assert client.post(f"/companies/{cid}/collect-now").status_code == 404
    assert client.get(f"/jobs/{jid}").status_code == 404
    assert client.get("/jobs", params={"status": "all"}).json() == {"total": 0, "items": []}
    assert client.get("/jobs", params={"company_id": cid}).json()["total"] == 0


@pytest.mark.db
def test_jobs_list_pagination_and_filters(client, uids):
    c1 = _company(uids[0], "a", "Alpha")
    c2 = _company(uids[0], "b", "Beta")
    base = datetime.now(timezone.utc)
    for i in range(5):
        _job(uids[0], c1, f"a{i}", title=f"Dev {i}", first_seen_at=base + timedelta(minutes=i))
    _job(uids[0], c2, "b0", title="Ops", first_seen_at=base + timedelta(hours=1))
    _job(uids[0], c2, "b1", title="Old", status="closed")
    r = client.get("/jobs", params={"limit": 2, "offset": 0}).json()
    assert r["total"] == 6
    assert [j["title"] for j in r["items"]] == ["Ops", "Dev 4"]
    assert r["items"][0]["company_name"] == "Beta"
    r2 = client.get("/jobs", params={"limit": 2, "offset": 2}).json()
    assert [j["title"] for j in r2["items"]] == ["Dev 3", "Dev 2"]
    assert client.get("/jobs", params={"company_id": c1}).json()["total"] == 5
    assert client.get("/jobs", params={"status": "closed"}).json()["total"] == 1
    assert client.get("/jobs", params={"status": "all"}).json()["total"] == 7
    assert client.get("/jobs", params={"status": "bogus"}).status_code == 422


@pytest.mark.db
def test_jobs_q_filter_escapes_wildcards(client, uids):
    cid = _company(uids[0])
    _job(uids[0], cid, "1", title="Senior Engineer")
    _job(uids[0], cid, "2", title="100% Remote")
    _job(uids[0], cid, "3", title="a_b role")
    _job(uids[0], cid, "4", title="axb role")

    def titles(q):
        return sorted(j["title"] for j in client.get("/jobs", params={"q": q}).json()["items"])

    assert titles("engineer") == ["Senior Engineer"]
    assert titles("%") == ["100% Remote"]
    assert titles("a_b") == ["a_b role"]
    assert client.get("/jobs", params={"q": "x" * 101}).status_code == 422


@pytest.mark.db
def test_jobs_list_excludes_description_and_raw(client, uids):
    cid = _company(uids[0])
    _job(uids[0], cid, "1")
    item = client.get("/jobs").json()["items"][0]
    assert "description" not in item and "raw" not in item and "embedding" not in item
    assert set(item) == {
        "id", "company_id", "company_name", "title", "url", "location_text", "country",
        "city", "work_model", "seniority", "language", "department", "salary_known",
        "status", "first_seen_at", "posted_at"}


@pytest.mark.db
def test_job_detail_has_clean_text(client, uids):
    from decimal import Decimal

    cid = _company(uids[0])
    jid = _job(uids[0], cid, "1", description="Texto limpo", salary_min=Decimal("1000.50"),
               salary_max=Decimal("2000"), salary_currency="BRL", salary_period="month",
               salary_known=True)
    r = client.get(f"/jobs/{jid}")
    assert r.status_code == 200
    b = r.json()
    assert b["description"] == "Texto limpo"
    assert b["salary_min"] == 1000.5 and b["salary_max"] == 2000.0
    assert b["salary_currency"] == "BRL" and b["salary_period"] == "month"
    assert b["salary_known"] is True
    assert "raw" not in b and "embedding" not in b
    for k in ("last_seen_at", "closed_at", "seniority", "language", "department", "posted_at"):
        assert k in b


@pytest.mark.db
@pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"offset": -1}])
def test_limit_cap(client, params):
    assert client.get("/jobs", params=params).status_code == 422


@pytest.mark.parametrize("order", [
    ["api.sources", "api.main"],
    ["api.main", "api.sources"],
    ["api.sources"],
])
def test_import_api_sources_fresh_subprocess(order):
    code = "; ".join(f"import {m}" for m in order)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=os.environ.copy(),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
