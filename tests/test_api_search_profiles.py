import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.models import SearchProfile, User

pytestmark = pytest.mark.db


@pytest.fixture
def env():
    created: list[int] = []

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"api-sp-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            return u.id

    def client(uid):
        app.dependency_overrides[current_user_id] = lambda: uid
        return TestClient(app)

    yield new_user, client
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(SearchProfile).where(SearchProfile.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


def _items(c):
    r = c.get("/search-profiles")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_empty_list_for_new_user(env):
    new_user, client = env
    assert _items(client(new_user())) == []


def test_create_copies_active_and_can_activate(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={"countries": ["br"], "query_text": "dados"})
    r = c.post("/search-profiles", json={"name": "  Remoto Europa "})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["name"] == "Remoto Europa" and new["active"] is False
    assert new["filters"]["countries"] == ["BR"] and new["query_text"] == "dados"
    items = _items(c)
    assert [i["active"] for i in items].count(True) == 1

    r = c.post(f"/search-profiles/{new['id']}/activate")
    assert r.status_code == 200
    c.put("/search-profile", json={"countries": ["de"]})  # edits the new active one
    by_name = {i["name"]: i for i in _items(c)}
    assert by_name["Remoto Europa"]["active"] is True
    assert by_name["Remoto Europa"]["filters"]["countries"] == ["DE"]
    assert by_name["default"]["filters"]["countries"] == ["BR"]  # untouched
    assert c.get("/search-profile").json()["filters"]["countries"] == ["DE"]


def test_create_with_activate_flag_and_without_prior_profile(env):
    new_user, client = env
    c = client(new_user())
    r = c.post("/search-profiles", json={"name": "Brasil CLT", "activate": True})
    assert r.status_code == 201, r.text
    assert r.json()["active"] is True
    items = _items(c)
    assert len(items) == 2 and [i["name"] for i in items if i["active"]] == ["Brasil CLT"]


def test_activate_switches_query_and_clears_nothing(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={"query_text": "gosto A"})
    other = c.post("/search-profiles", json={"name": "B"}).json()
    c.post(f"/search-profiles/{other['id']}/activate")
    assert c.get("/search-profile").json()["query_text"] == "gosto A"  # copied
    c.put("/search-profile", json={"query_text": "gosto B"})
    default = next(i for i in _items(c) if i["name"] == "default")
    c.post(f"/search-profiles/{default['id']}/activate")
    assert c.get("/search-profile").json()["query_text"] == "gosto A"


def test_name_rules(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={})
    assert c.post("/search-profiles", json={"name": "   "}).status_code == 422
    assert c.post("/search-profiles", json={"name": "x" * 61}).status_code == 422
    assert c.post("/search-profiles", json={"name": "a\nb"}).status_code == 422
    assert c.post("/search-profiles", json={"name": "A", "bogus": 1}).status_code == 422
    assert c.post("/search-profiles", json={"name": "Alfa"}).status_code == 201
    r = c.post("/search-profiles", json={"name": "alfa"})  # case-insensitive
    assert r.status_code == 409
    assert "nome" in r.json()["detail"].lower()


def test_limit_of_profiles(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={})
    for i in range(19):
        assert c.post("/search-profiles", json={"name": f"p{i}"}).status_code == 201
    r = c.post("/search-profiles", json={"name": "extra"})
    assert r.status_code == 409


def test_rename(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={})
    a = c.post("/search-profiles", json={"name": "A"}).json()
    b = c.post("/search-profiles", json={"name": "B"}).json()
    r = c.patch(f"/search-profiles/{a['id']}", json={"name": " Novo "})
    assert r.status_code == 200 and r.json()["name"] == "Novo"
    assert c.patch(f"/search-profiles/{b['id']}", json={"name": "novo"}).status_code == 409
    assert c.patch(f"/search-profiles/{b['id']}", json={"name": "B"}).status_code == 200  # same
    active = next(i for i in _items(c) if i["active"])
    assert c.patch(f"/search-profiles/{active['id']}", json={"name": "Principal"}).status_code == 200
    # the active one still gets saved normally after being renamed
    assert c.put("/search-profile", json={"countries": ["br"]}).status_code == 200
    assert len(_items(c)) == 3


def test_delete_rules(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={})
    a = c.post("/search-profiles", json={"name": "A"}).json()
    active = next(i for i in _items(c) if i["active"])
    r = c.delete(f"/search-profiles/{active['id']}")
    assert r.status_code == 409
    assert c.delete(f"/search-profiles/{a['id']}").status_code == 204
    assert c.delete(f"/search-profiles/{a['id']}").status_code == 404
    assert len(_items(c)) == 1


def test_other_users_profiles_are_404(env):
    new_user, client = env
    owner, other = new_user(), new_user()
    co = client(owner)
    co.put("/search-profile", json={})
    pid = co.post("/search-profiles", json={"name": "A"}).json()["id"]
    cx = client(other)
    assert _items(cx) == []
    assert cx.post(f"/search-profiles/{pid}/activate").status_code == 404
    assert cx.patch(f"/search-profiles/{pid}", json={"name": "Z"}).status_code == 404
    assert cx.delete(f"/search-profiles/{pid}").status_code == 404
    with SessionLocal() as s:
        assert s.scalar(select(SearchProfile.name).where(SearchProfile.id == pid)) == "A"


def test_activate_when_already_active_is_ok(env):
    new_user, client = env
    c = client(new_user())
    c.put("/search-profile", json={})
    active = _items(c)[0]
    assert c.post(f"/search-profiles/{active['id']}/activate").status_code == 200
    assert _items(c)[0]["active"] is True
