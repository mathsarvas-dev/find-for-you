import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.models import (
    Company, Job, JobLabel, Profile, ProfileChunk, User, UserPreference,
)

DIM = 1024


def e(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


@pytest.fixture
def env():
    created: list[int] = []
    counter = iter(range(100000))

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"api-qual-{uuid.uuid4().hex}@example.test")
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
        kw.setdefault("description", "Texto completo")
        with SessionLocal() as s:
            j = Job(
                user_id=uid, company_id=cid, external_id=str(n),
                url=f"https://example.test/{n}",
                raw={"big": "blob"}, content_hash=f"h{n}", fingerprint=f"f{n}",
                embedding=emb, model_version=MODEL_VERSION if emb is not None else None,
                **kw,
            )
            s.add(j)
            s.commit()
            return j.id

    def add_profile(uid, emb=None):
        with SessionLocal() as s:
            s.add(Profile(user_id=uid, version=1, data={}, content_hash="h"))
            s.add(ProfileChunk(
                user_id=uid, profile_version=1, source_ref="r0", kind="note",
                text="t", content_hash="h", embedding=emb,
                model_version=MODEL_VERSION if emb is not None else None,
            ))
            s.commit()

    yield new_user, add_job, add_profile
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(JobLabel).where(JobLabel.user_id == uid))
            s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
            s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
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


def _ready(env, n=6, **job_kw):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, e(0))
    ids = [add_job(uid, cid, e(0), **job_kw) for _ in range(n)]
    return uid, cid, ids


def _count(uid):
    with SessionLocal() as s:
        return len(s.scalars(select(JobLabel).where(JobLabel.user_id == uid)).all())


@pytest.mark.db
def test_sample_stable_between_visits(env, as_user):
    uid, _, ids = _ready(env, 8)
    c = as_user(uid)
    a = c.get("/quality/sample")
    assert a.status_code == 200, a.text
    b = c.get("/quality/sample")
    assert [i["job_id"] for i in a.json()["items"]] == [i["job_id"] for i in b.json()["items"]]
    assert set(a.json()) == {"items", "labeled_total"}
    assert a.json()["items"]
    item = a.json()["items"][0]
    assert set(item) == {"job_id", "title", "company_name", "country", "url",
                         "snippet", "label"}
    assert item["company_name"] == "Acme" and item["label"] is None


@pytest.mark.db
def test_snippet_is_first_400_chars_and_url_passthrough(env, as_user):
    uid, _, ids = _ready(env, 2, description="<b>x</b>" + "a" * 600)
    items = as_user(uid).get("/quality/sample").json()["items"]
    assert items and all(len(i["snippet"]) == 400 for i in items)
    assert items[0]["snippet"].startswith("<b>x</b>")
    assert items[0]["url"].startswith("https://example.test/")


@pytest.mark.db
def test_new_sample_keeps_labels(env, as_user):
    uid, _, ids = _ready(env, 8)
    c = as_user(uid)
    items = c.get("/quality/sample").json()["items"]
    jid = items[0]["job_id"]
    assert c.put(f"/quality/labels/{jid}", json={"label": "good"}).status_code == 200
    assert {i["job_id"]: i["label"] for i in c.get("/quality/sample").json()["items"]}[jid] == "good"
    r = c.post("/quality/sample/new")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["labeled_total"] == 1
    got = {i["job_id"]: i["label"] for i in body["items"]}
    assert got.get(jid, "good") == "good"
    assert c.get("/quality/sample").json()["labeled_total"] == 1


@pytest.mark.db
def test_label_upsert_unique_per_job(env, as_user):
    uid, _, ids = _ready(env, 2)
    c = as_user(uid)
    assert c.put(f"/quality/labels/{ids[0]}", json={"label": "good"}).status_code == 200
    r = c.put(f"/quality/labels/{ids[0]}", json={"label": "bad"})
    assert r.status_code == 200 and r.json() == {"job_id": ids[0], "label": "bad"}
    with SessionLocal() as s:
        rows = s.scalars(select(JobLabel).where(JobLabel.user_id == uid)).all()
        assert len(rows) == 1 and rows[0].label == "bad"


@pytest.mark.db
def test_label_other_user_job_404(env, as_user):
    _, _, ids = _ready(env, 1)
    uid2, _, _ = _ready(env, 1)
    c = as_user(uid2)
    for call in (
        lambda j: c.put(f"/quality/labels/{j}", json={"label": "good"}),
        lambda j: c.delete(f"/quality/labels/{j}"),
    ):
        for j in (ids[0], 999999999, 2**31, 2**40, 0, -1):
            r = call(j)
            assert r.status_code == 404, (j, r.text)
            assert r.json()["detail"] == "Vaga não encontrada."
    assert _count(uid2) == 0


@pytest.mark.db
def test_label_invalid_value_422(env, as_user):
    uid, _, ids = _ready(env, 1)
    c = as_user(uid)
    assert c.put(f"/quality/labels/{ids[0]}", json={"label": "meh"}).status_code == 422
    assert c.put(f"/quality/labels/{ids[0]}", json={}).status_code == 422
    assert c.put(f"/quality/labels/{ids[0]}",
                 json={"label": "good", "x": 1}).status_code == 422
    assert _count(uid) == 0


@pytest.mark.db
def test_delete_label_204_and_idempotent(env, as_user):
    uid, _, ids = _ready(env, 1)
    c = as_user(uid)
    c.put(f"/quality/labels/{ids[0]}", json={"label": "good"})
    r = c.delete(f"/quality/labels/{ids[0]}")
    assert r.status_code == 204 and r.content == b""
    assert _count(uid) == 0
    assert c.delete(f"/quality/labels/{ids[0]}").status_code == 204


@pytest.mark.db
def test_report_insufficient_message(env, as_user):
    uid, _, ids = _ready(env, 2)
    c = as_user(uid)
    c.put(f"/quality/labels/{ids[0]}", json={"label": "good"})
    r = c.get("/quality/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "insuficiente"
    assert any("faltam" in m for m in body["missing"])
    assert body["precision_at_10"] is None and body["misranked"] == []
    assert body["target"] == 0.6


@pytest.mark.db
def test_sample_requires_vectorized_profile_409(env, as_user):
    from api.matching import MSG_NO_PROFILE, MSG_NOT_VECTORIZED

    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_job(uid, cid, e(0))
    c = as_user(uid)
    for r in (c.get("/quality/sample"), c.post("/quality/sample/new")):
        assert r.status_code == 409 and r.json()["detail"] == MSG_NO_PROFILE
    add_profile(uid, None)
    for r in (c.get("/quality/sample"), c.post("/quality/sample/new")):
        assert r.status_code == 409 and r.json()["detail"] == MSG_NOT_VECTORIZED
