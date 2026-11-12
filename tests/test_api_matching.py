import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

import api.matching as api_matching
from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.models import (
    Company, Job, Match, Profile, ProfileChunk, SearchProfile, User,
)

ROOT = Path(__file__).resolve().parents[1]
DIM = 1024


def e(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    created: list[int] = []
    counter = iter(range(100000))

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"api-match-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            c = Company(user_id=u.id, source="greenhouse",
                        identifier=f"acme{u.id}", name="Acme")
            s.add(c)
            s.commit()
            return u.id, c.id

    def add_job(uid, cid, emb=None, **kw):
        n = next(counter)
        kw.setdefault("title", f"t{n}")
        with SessionLocal() as s:
            j = Job(
                user_id=uid, company_id=cid, external_id=str(n),
                url=f"https://example.test/{n}", description="Texto completo",
                raw={"big": "blob"}, content_hash=f"h{n}", fingerprint=f"f{n}",
                embedding=emb, model_version=MODEL_VERSION if emb is not None else None,
                **kw,
            )
            s.add(j)
            s.commit()
            return j.id

    def add_profile(uid, version=1, emb=None, model_version=MODEL_VERSION):
        with SessionLocal() as s:
            s.add(Profile(user_id=uid, version=version, data={}, content_hash="h"))
            s.add(ProfileChunk(
                user_id=uid, profile_version=version, source_ref="r0", kind="note",
                text="t", content_hash="h", embedding=emb,
                model_version=model_version if emb is not None else None,
            ))
            s.commit()

    yield new_user, add_job, add_profile
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(Match).where(Match.user_id == uid))
            s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(SearchProfile).where(SearchProfile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def as_user(clock):
    limiter = api_matching.RefreshLimiter(interval=10.0, clock=clock)
    app.dependency_overrides[api_matching.get_refresh_limiter] = lambda: limiter

    def _as(uid):
        app.dependency_overrides[current_user_id] = lambda: uid
        return TestClient(app)

    yield _as
    app.dependency_overrides.clear()


@pytest.mark.db
def test_put_then_get_search_profile_roundtrip(env, as_user):
    new_user, _, _ = env
    uid, _ = new_user()
    c = as_user(uid)
    r = c.get("/search-profile")
    assert r.status_code == 200, r.text
    assert r.json()["filters"]["countries"] == []
    assert r.json()["filters"]["include_unknown"] is True
    with SessionLocal() as s:  # GET does not create the row
        assert s.scalars(select(SearchProfile).where(SearchProfile.user_id == uid)).all() == []

    body = {"countries": ["br"], "work_models": ["remote"], "salary_min": 100000,
            "salary_currency": "brl", "include_unknown": False}
    r = c.put("/search-profile", json=body)
    assert r.status_code == 200, r.text
    f = r.json()["filters"]
    assert f["countries"] == ["BR"] and f["salary_currency"] == "BRL"
    assert c.get("/search-profile").json()["filters"] == f

    r = c.put("/search-profile", json={"seniorities": ["senior"]})
    assert r.status_code == 200
    assert c.get("/search-profile").json()["filters"]["countries"] == []
    assert c.get("/search-profile").json()["filters"]["seniorities"] == ["senior"]
    with SessionLocal() as s:
        rows = s.scalars(select(SearchProfile).where(SearchProfile.user_id == uid)).all()
        assert len(rows) == 1 and rows[0].is_default is True
        assert rows[0].filters["seniorities"] == ["senior"]


@pytest.mark.db
def test_put_rejects_invalid_enum_and_extra_fields(env, as_user):
    new_user, _, _ = env
    uid, _ = new_user()
    c = as_user(uid)
    assert c.put("/search-profile", json={"work_models": ["space"]}).status_code == 422
    assert c.put("/search-profile", json={"bogus": 1}).status_code == 422
    assert c.put("/search-profile", json={"salary_min": 10}).status_code == 422
    with SessionLocal() as s:
        assert s.scalars(select(SearchProfile).where(SearchProfile.user_id == uid)).all() == []


@pytest.mark.db
def test_put_name_conflict_maps_to_409(env, as_user):
    new_user, _, _ = env
    uid, _ = new_user()
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name=api_matching.DEFAULT_PROFILE_NAME,
                            filters={}, is_default=False))
        s.commit()
    r = as_user(uid).put("/search-profile", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == api_matching.MSG_PROFILE_CONFLICT


@pytest.mark.db
def test_invalid_saved_filters_map_to_409(env, as_user):
    new_user, _, add_profile = env
    uid, _ = new_user()
    add_profile(uid, emb=e(0))
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name="x", filters={"work_models": ["space"]},
                            is_default=True))
        s.commit()
    c = as_user(uid)
    for r in (c.get("/search-profile"), c.get("/matches"), c.post("/matches/refresh")):
        assert r.status_code == 409
        assert r.json()["detail"] == api_matching.MSG_BAD_SAVED_FILTERS
    assert c.put("/search-profile", json={}).status_code == 200  # recovers


@pytest.mark.db
def test_get_matches_applies_saved_filters(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=e(0))
    remote = add_job(uid, cid, e(0), work_model="remote")
    add_job(uid, cid, e(0), work_model="onsite")
    c = as_user(uid)
    assert c.get("/matches").json()["total"] == 2
    c.put("/search-profile", json={"work_models": ["remote"], "include_unknown": False})
    body = c.get("/matches").json()
    assert body["total"] == 1
    assert [i["job"]["id"] for i in body["items"]] == [remote]
    item = body["items"][0]
    assert body["vectorized"] is True and body["profile_version"] == 1
    assert body["chunks_used"] == 1
    assert item["vec_score"] == pytest.approx(1.0, abs=1e-6)
    assert item["ai_score"] is None and item["state"] is None
    assert item["job"]["company_name"] == "Acme"
    assert "description" not in item["job"] and "raw" not in item["job"]


@pytest.mark.db
def test_get_matches_not_vectorized_without_chunks(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=None)
    add_job(uid, cid, None)
    body = as_user(uid).get("/matches").json()
    assert body["vectorized"] is False
    assert body["total"] == 0 and body["items"] == []
    assert body["profile_version"] == 1
    assert body["chunks_used"] == 0
    assert body["missing_embeddings"] == 1


@pytest.mark.db
def test_get_matches_limit_bounds_422(env, as_user):
    new_user, _, _ = env
    uid, _ = new_user()
    c = as_user(uid)
    assert c.get("/matches?limit=0").status_code == 422
    assert c.get("/matches?limit=101").status_code == 422
    assert c.get("/matches?offset=-1").status_code == 422
    assert c.get("/matches?status=duplicate").status_code == 422
    assert c.get("/matches?limit=100&status=all").status_code == 200


@pytest.mark.db
def test_matches_never_leak_other_user(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    other, ocid = new_user()
    add_profile(uid, emb=e(0))
    add_profile(other, emb=e(0))
    mine = add_job(uid, cid, e(0))
    add_job(other, ocid, e(0))
    as_user(other).put("/search-profile", json={"work_models": ["remote"],
                                                "include_unknown": False})
    body = as_user(uid).get("/matches?status=all").json()
    assert [i["job"]["id"] for i in body["items"]] == [mine]
    assert body["total"] == 1
    # other user's saved filters are not applied to me
    assert as_user(uid).get("/search-profile").json()["filters"]["work_models"] == []


def _set_match(uid, jid, **values):
    with SessionLocal() as s:
        s.execute(update(Match).where(Match.user_id == uid, Match.job_id == jid)
                  .values(**values))
        s.commit()


@pytest.mark.db
def test_match_detail_includes_rationale_for_ok(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=e(0))
    jid = add_job(uid, cid, e(0), salary_known=True, salary_min=1000,
                  salary_currency="BRL", salary_period="month")
    c = as_user(uid)
    assert c.post("/matches/refresh").status_code == 200
    _set_match(uid, jid, state="ok", ai_score=87, rationale="Bom encaixe.",
               strengths=["Python"], gaps=["Go"])
    r = c.get(f"/matches/{jid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == jid and d["description"] == "Texto completo"
    assert d["salary_min"] == 1000.0
    assert d["ai_score"] == 87 and d["state"] == "ok"
    assert d["rationale"] == "Bom encaixe."
    assert d["strengths"] == ["Python"] and d["gaps"] == ["Go"]
    assert d["error"] is None
    assert d["vec_score"] == pytest.approx(1.0, abs=1e-6)
    assert "raw" not in d
    listed = c.get("/matches").json()["items"][0]
    assert listed["ai_score"] == 87 and listed["state"] == "ok"


@pytest.mark.db
def test_match_detail_without_match_row(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=e(0))
    jid = add_job(uid, cid, None)
    d = as_user(uid).get(f"/matches/{jid}").json()
    assert d["vec_score"] is None and d["state"] is None and d["ai_score"] is None
    assert d["unknown_fields"] == ["salary", "work_model", "country", "seniority", "language"]


@pytest.mark.db
def test_match_detail_404_other_user(env, as_user):
    new_user, add_job, add_profile = env
    uid, _ = new_user()
    other, ocid = new_user()
    add_profile(other, emb=e(0))
    theirs = add_job(other, ocid, e(0))
    as_user(other).post("/matches/refresh")
    r = as_user(uid).get(f"/matches/{theirs}")
    assert r.status_code == 404
    assert as_user(uid).get("/matches/999999999").status_code == 404


@pytest.mark.db
def test_refresh_creates_pending_rows(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=e(0))
    a = add_job(uid, cid, e(0))
    b = add_job(uid, cid, e(1))
    add_job(uid, cid, e(2), status="closed")
    r = as_user(uid).post("/matches/refresh")
    assert r.status_code == 200, r.text
    assert r.json() == {"requested": 2, "created": 2, "reset": 0,
                        "already_ok": 0, "profile_version": 1}
    with SessionLocal() as s:
        rows = s.scalars(select(Match).where(Match.user_id == uid)).all()
        assert sorted(m.job_id for m in rows) == sorted([a, b])
        assert all(m.profile_version == 1 for m in rows)


@pytest.mark.db
def test_refresh_uses_saved_filters(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=e(0))
    remote = add_job(uid, cid, e(0), work_model="remote")
    add_job(uid, cid, e(0), work_model="onsite")
    c = as_user(uid)
    c.put("/search-profile", json={"work_models": ["remote"], "include_unknown": False})
    assert c.post("/matches/refresh").json()["created"] == 1
    with SessionLocal() as s:
        assert s.scalars(select(Match.job_id).where(Match.user_id == uid)).all() == [remote]


@pytest.mark.db
def test_refresh_rate_limited_429(env, as_user, clock):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    other, ocid = new_user()
    add_profile(uid, emb=e(0))
    add_profile(other, emb=e(0))
    add_job(uid, cid, e(0))
    assert as_user(uid).post("/matches/refresh").status_code == 200
    clock.t += 9.9
    r = as_user(uid).post("/matches/refresh")
    assert r.status_code == 429
    assert r.json()["detail"]
    assert as_user(other).post("/matches/refresh").status_code == 200  # per user
    clock.t += 0.1
    assert as_user(uid).post("/matches/refresh").status_code == 200


def test_refresh_limiter_unit():
    clock = FakeClock()
    lim = api_matching.RefreshLimiter(interval=10.0, clock=clock)
    assert lim.hit(1) is True
    assert lim.hit(1) is False
    assert lim.hit(2) is True
    clock.t += 10
    assert lim.hit(1) is True
    lim.reset()
    assert lim.hit(1) is True


@pytest.mark.db
def test_refresh_without_profile_409(env, as_user):
    new_user, add_job, _ = env
    uid, cid = new_user()
    add_job(uid, cid, e(0))
    r = as_user(uid).post("/matches/refresh")
    assert r.status_code == 409
    assert r.json() == {"detail": "Confirme seu perfil antes de avaliar vagas."}
    with SessionLocal() as s:
        assert s.scalars(select(Match).where(Match.user_id == uid)).all() == []


@pytest.mark.db
def test_refresh_profile_not_vectorized_409(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=None)
    add_job(uid, cid, e(0))
    r = as_user(uid).post("/matches/refresh")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail and detail != "Confirme seu perfil antes de avaliar vagas."


@pytest.mark.db
def test_unknown_fields_marked(env, as_user):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, emb=e(0))
    known = add_job(uid, cid, e(0), work_model="remote", seniority="senior",
                    language="pt", country="BR", salary_known=True,
                    salary_min=1, salary_currency="BRL", salary_period="year")
    unknown = add_job(uid, cid, e(1))
    partial = add_job(uid, cid, e(2), work_model="hybrid", country="DE")
    items = {i["job"]["id"]: i for i in as_user(uid).get("/matches").json()["items"]}
    assert items[known]["unknown_fields"] == []
    assert items[unknown]["unknown_fields"] == [
        "salary", "work_model", "country", "seniority", "language"]
    assert items[partial]["unknown_fields"] == ["salary", "seniority", "language"]


def test_cors_allows_put():
    r = TestClient(app).options(
        "/search-profile",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert "PUT" in r.headers["access-control-allow-methods"]


def test_model_version_constant_matches_embedder_without_torch():
    code = (
        "import sys, api.main, api.matching\n"
        "from rumo_core.embeddings.bge_m3 import MODEL_VERSION, BgeM3Embedder\n"
        "assert MODEL_VERSION == 'bge-m3@1:max512', MODEL_VERSION\n"
        "assert BgeM3Embedder().model_version == MODEL_VERSION\n"
        "assert api.matching.MODEL_VERSION == MODEL_VERSION\n"
        "assert 'torch' not in sys.modules and 'sentence_transformers' not in sys.modules\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=os.environ.copy(),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
