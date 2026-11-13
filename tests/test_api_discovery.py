import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.main import app, current_user_id
from rumo_core.config import get_settings
from rumo_core.db import SessionLocal
from rumo_core.models import Company, DiscoveryCandidate, User, UserPreference


@pytest.fixture
def env():
    created: list[int] = []

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"api-disc-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            return u.id

    def add_cand(uid, token, state="pending", kind="catalog", **kw):
        with SessionLocal() as s:
            s.add(DiscoveryCandidate(
                user_id=uid, source="greenhouse", token=token, state=state,
                origin_kind=kind, **kw,
            ))
            s.commit()

    def add_company(uid, identifier, origin="manual", active=True):
        with SessionLocal() as s:
            s.add(Company(user_id=uid, source="greenhouse", identifier=identifier,
                          name=identifier, origin=origin, active=active))
            s.commit()

    yield new_user, add_cand, add_company
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid))
            s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


@pytest.fixture
def as_user():
    def _as(uid):
        app.dependency_overrides[current_user_id] = lambda: uid
        return TestClient(app)

    yield _as
    app.dependency_overrides.clear()


@pytest.mark.db
def test_get_defaults(env, as_user):
    new_user, _, _ = env
    uid = new_user()
    r = as_user(uid).get("/discovery")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "enabled": True, "target_companies": [], "auto_companies": 0,
        "max_auto": get_settings().discovery_max_auto_companies,
        "pending": 0, "found": 0,
    }
    with SessionLocal() as s:  # GET does not create the row
        assert s.scalars(
            select(UserPreference).where(UserPreference.user_id == uid)
        ).all() == []


@pytest.mark.db
def test_put_roundtrip_and_trims(env, as_user):
    new_user, _, _ = env
    uid = new_user()
    c = as_user(uid)
    r = c.put("/discovery", json={
        "enabled": False,
        "target_companies": ["  Acme  ", "acme", "Globex", "ACME", "Initech "],
    })
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert r.json()["target_companies"] == ["Acme", "Globex", "Initech"]
    assert c.get("/discovery").json() == r.json()
    r = c.put("/discovery", json={"enabled": True, "target_companies": []})
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["target_companies"] == []
    with SessionLocal() as s:
        assert len(s.scalars(
            select(UserPreference).where(UserPreference.user_id == uid)
        ).all()) == 1


@pytest.mark.db
def test_put_rejects_bad_names_and_too_many(env, as_user):
    new_user, _, _ = env
    c = as_user(new_user())
    ok = {"enabled": True}
    bad = [
        {**ok, "target_companies": [""]},
        {**ok, "target_companies": ["   "]},
        {**ok, "target_companies": ["x" * 81]},
        {**ok, "target_companies": ["a\x00b"]},
        {**ok, "target_companies": ["a\nb"]},
        {**ok, "target_companies": ["a\x07b"]},
        {**ok, "target_companies": [f"n{i}" for i in range(51)]},
        {**ok, "target_companies": [1]},
        {**ok, "target_companies": [], "extra": 1},
        {"target_companies": []},
        {"enabled": True},
    ]
    for body in bad:
        r = c.put("/discovery", json=body)
        assert r.status_code == 422, body
    # boundaries are accepted
    r = c.put("/discovery", json={**ok, "target_companies": ["x" * 80]})
    assert r.status_code == 200
    r = c.put("/discovery", json={**ok, "target_companies": [f"n{i}" for i in range(50)]})
    assert r.status_code == 200 and len(r.json()["target_companies"]) == 50
    # the cap applies to the submitted list, before deduplication
    assert c.put("/discovery", json={
        **ok, "target_companies": ["a"] * 51}).status_code == 422


@pytest.mark.db
def test_counts_reflect_candidates_and_auto_companies(env, as_user):
    new_user, add_cand, add_company = env
    uid = new_user()
    add_company(uid, "m1", "manual")
    add_company(uid, "a1", "auto")
    add_company(uid, "a2", "auto")
    add_company(uid, "a3", "auto", active=False)
    add_cand(uid, "p1", "pending")
    add_cand(uid, "p2", "pending")
    add_cand(uid, "v1", "valid")
    add_cand(uid, "i1", "invalid")
    add_cand(uid, "k1", "known")
    j = as_user(uid).get("/discovery").json()
    assert (j["auto_companies"], j["pending"], j["found"]) == (2, 2, 1)


@pytest.mark.db
def test_candidates_list_filters_state_and_paginates(env, as_user):
    new_user, add_cand, _ = env
    uid = new_user()
    add_cand(uid, "t1", "pending", name_hint="One", attempts=1, last_error="boom")
    add_cand(uid, "t2", "valid", kind="profile")
    add_cand(uid, "t3", "pending", kind="target")
    add_cand(uid, "t4", "rejected")
    c = as_user(uid)
    r = c.get("/discovery/candidates")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 4
    assert [i["token"] for i in j["items"]] == ["t1", "t2", "t3", "t4"]
    assert j["items"][0] == {
        "token": "t1", "name_hint": "One", "origin_kind": "catalog",
        "state": "pending", "attempts": 1, "last_error": "boom",
    }
    j = c.get("/discovery/candidates", params={"state": "pending"}).json()
    assert j["total"] == 2 and [i["token"] for i in j["items"]] == ["t1", "t3"]
    j = c.get("/discovery/candidates", params={"limit": 2, "offset": 1}).json()
    assert j["total"] == 4 and [i["token"] for i in j["items"]] == ["t2", "t3"]
    for params in ({"state": "bogus"}, {"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert c.get("/discovery/candidates", params=params).status_code == 422


@pytest.mark.db
def test_scoped_by_user(env, as_user):
    new_user, add_cand, add_company = env
    a, b = new_user(), new_user()
    add_cand(a, "secret", "valid")
    add_company(a, "autoa", "auto")
    as_user(a).put("/discovery", json={"enabled": False, "target_companies": ["Zed"]})
    c = as_user(b)
    j = c.get("/discovery").json()
    assert j["enabled"] is True and j["target_companies"] == []
    assert (j["auto_companies"], j["pending"], j["found"]) == (0, 0, 0)
    assert c.get("/discovery/candidates").json() == {"total": 0, "items": []}


@pytest.mark.db
def test_companies_expose_origin_and_filter(env, as_user):
    new_user, _, add_company = env
    uid = new_user()
    add_company(uid, "man", "manual")
    add_company(uid, "aut", "auto")
    c = as_user(uid)
    rows = c.get("/companies").json()
    assert {r["identifier"]: r["origin"] for r in rows} == {"man": "manual", "aut": "auto"}
    assert [r["identifier"] for r in c.get("/companies", params={"origin": "auto"}).json()] == ["aut"]
    assert [r["identifier"] for r in c.get("/companies", params={"origin": "manual"}).json()] == ["man"]
    assert c.get("/companies", params={"origin": "x"}).status_code == 422
