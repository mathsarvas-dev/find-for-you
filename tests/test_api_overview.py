import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.models import (
    Company, InterviewSession, Job, JobFeed, Match, Profile, ProfileChunk, User,
)

DIM = 1024
CONTRACT_KEYS = {"profile", "interview", "companies", "jobs", "matches", "feeds"}


def vec():
    v = [0.0] * DIM
    v[0] = 1.0
    return v


@pytest.fixture
def env():
    created: list[int] = []
    counter = iter(range(100000))

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"api-overview-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            return u.id

    def add_company(uid, **kw):
        n = next(counter)
        with SessionLocal() as s:
            c = Company(user_id=uid, source="greenhouse",
                        identifier=f"ov{uid}-{n}", name=f"Co{n}", **kw)
            s.add(c)
            s.commit()
            return c.id

    def add_job(uid, cid, status="open", content_hash=None):
        n = next(counter)
        with SessionLocal() as s:
            j = Job(
                user_id=uid, company_id=cid, external_id=str(n),
                url=f"https://example.test/{n}", title=f"SECRET-TITLE-{n}",
                description="SECRET-DESCRIPTION", raw={}, status=status,
                content_hash=content_hash or f"h{n}", fingerprint=f"f{n}",
            )
            s.add(j)
            s.commit()
            return j.id, j.content_hash

    def add_profile(uid, version=1, data=None, embedded=False):
        with SessionLocal() as s:
            s.add(Profile(user_id=uid, version=version,
                          data={} if data is None else data, content_hash="h"))
            s.add(ProfileChunk(
                user_id=uid, profile_version=version, source_ref="r0",
                kind="note", text="t", content_hash="h",
                embedding=vec() if embedded else None,
                model_version=MODEL_VERSION if embedded else None,
            ))
            s.commit()

    def add_match(uid, jid, version, state, content_hash):
        with SessionLocal() as s:
            s.add(Match(user_id=uid, job_id=jid, profile_version=version,
                        vec_score=0.5, state=state, content_hash=content_hash))
            s.commit()

    def add_session(uid, version, state):
        with SessionLocal() as s:
            s.add(InterviewSession(user_id=uid, profile_version=version,
                                   state=state))
            s.commit()

    yield (new_user, add_company, add_job, add_profile, add_match, add_session)
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(Match).where(Match.user_id == uid))
            s.execute(delete(InterviewSession).where(InterviewSession.user_id == uid))
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


@pytest.mark.db
def test_overview_empty_user_defaults(env, as_user):
    uid = env[0]()
    r = as_user(uid).get("/overview")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "profile": {"confirmed": False, "version": None, "vectorized": False, "provisional": False,
                    "name": None, "email": None},
        "interview": {"state": "none"},
        "companies": {"total": 0, "active": 0, "auto": 0},
        "jobs": {"open": 0, "outside_filters": 0},
        "matches": {"scored": 0, "pending": 0},
        "feeds": {"active": 0},
    }


@pytest.mark.db
def test_overview_feeds_active(env, as_user):
    new_user = env[0]
    uid = new_user()
    with SessionLocal() as s:
        s.add_all([
            JobFeed(user_id=uid, source="remotive", params={"keywords": ["a"]}, active=True),
            JobFeed(user_id=uid, source="remoteok", params={"keywords": ["b"]}, active=False),
        ])
        s.commit()
    try:
        assert as_user(uid).get("/overview").json()["feeds"] == {"active": 1}
    finally:
        with SessionLocal() as s:
            s.execute(delete(JobFeed).where(JobFeed.user_id == uid))
            s.commit()


@pytest.mark.db
def test_profile_confirmed_and_vectorized_flags(env, as_user):
    new_user, add_profile = env[0], env[3]
    u1, u2 = new_user(), new_user()
    add_profile(u1, version=3, embedded=False)
    add_profile(u2, version=2, embedded=True)
    p1 = as_user(u1).get("/overview").json()["profile"]
    assert p1["confirmed"] is True and p1["version"] == 3
    assert p1["vectorized"] is False
    p2 = as_user(u2).get("/overview").json()["profile"]
    assert p2["confirmed"] is True and p2["version"] == 2
    assert p2["vectorized"] is True


@pytest.mark.db
@pytest.mark.parametrize("data,name,email", [
    ({"basics": {"name": "  Ana Silva  ", "email": " a@x.test "}},
     "Ana Silva", "a@x.test"),
    ({"basics": {"name": "x" * 500, "email": "y" * 300}}, "x" * 200, "y" * 200),
    ({"basics": {"name": "   ", "email": ""}}, None, None),
    ({"basics": {"name": 5, "email": ["a"]}}, None, None),
    ({"basics": "oops"}, None, None),
    ({}, None, None),
])
def test_name_email_trimmed_capped_and_nullable(env, as_user, data, name, email):
    uid = env[0]()
    env[3](uid, data=data)
    p = as_user(uid).get("/overview").json()["profile"]
    assert p["name"] == name and p["email"] == email


@pytest.mark.db
def test_companies_and_jobs_counts_scoped_by_user(env, as_user):
    new_user, add_company, add_job = env[0], env[1], env[2]
    u1, u2 = new_user(), new_user()
    c1 = add_company(u1)
    add_company(u1, active=False)
    add_company(u1, origin="auto")
    c_other = add_company(u2, origin="auto")
    add_job(u1, c1)
    add_job(u1, c1)
    add_job(u1, c1, status="closed")
    add_job(u1, c1, status="duplicate")
    add_job(u2, c_other)
    b = as_user(u1).get("/overview").json()
    assert b["companies"] == {"total": 3, "active": 2, "auto": 1}
    assert b["jobs"] == {"open": 2, "outside_filters": 0}


@pytest.mark.db
def test_matches_scored_pending_current_version_only(env, as_user):
    new_user, add_company, add_job, add_profile, add_match, _ = env
    uid, other = new_user(), new_user()
    add_profile(uid, version=1)
    add_profile(uid, version=2)
    c = add_company(uid)
    j1, h1 = add_job(uid, c)
    j2, h2 = add_job(uid, c)
    j3, h3 = add_job(uid, c)
    j4, h4 = add_job(uid, c)
    j5, h5 = add_job(uid, c)
    add_match(uid, j1, 2, "ok", h1)        # scored
    add_match(uid, j2, 2, "ok", "stale")   # stale -> not counted
    add_match(uid, j3, 2, "pendente", h3)  # pending
    add_match(uid, j4, 2, "erro", h4)      # neither
    add_match(uid, j5, 1, "ok", h5)        # old version
    jo, ho = add_job(uid, c)
    add_match(uid, jo, 1, "pendente", ho)  # old version pending
    # another user's data must not leak
    oc = add_company(other)
    oj, oh = add_job(other, oc)
    add_profile(other, version=2)
    add_match(other, oj, 2, "ok", oh)
    m = as_user(uid).get("/overview").json()["matches"]
    assert m == {"scored": 1, "pending": 1}


@pytest.mark.db
def test_interview_state_mapping(env, as_user):
    new_user, add_profile, add_session = env[0], env[3], env[5]
    none_u, prog_u, done_u, newer_u, mixed_u, err_u = (new_user() for _ in range(6))
    for u in (prog_u, done_u, mixed_u, err_u):
        add_profile(u, version=2)
    add_profile(newer_u, version=1)
    add_profile(newer_u, version=2)  # suggestion accepted -> N+1
    add_session(prog_u, 2, "awaiting_answer")
    add_session(done_u, 2, "done")
    add_session(newer_u, 1, "done")   # done on N while current is N+1
    add_session(mixed_u, 2, "done")
    add_session(mixed_u, 2, "generating")  # one done plus one active
    add_session(err_u, 2, "error")

    def state(u):
        return as_user(u).get("/overview").json()["interview"]["state"]

    assert state(none_u) == "none"
    assert state(prog_u) == "in_progress"
    assert state(done_u) == "done"
    assert state(newer_u) == "done"
    assert state(mixed_u) == "in_progress"
    assert state(err_u) == "in_progress"


@pytest.mark.db
def test_matches_only_count_open_jobs(env, as_user):
    new_user, add_company, add_job, add_profile, add_match, _ = env
    uid = new_user()
    add_profile(uid, version=1)
    c = add_company(uid)
    jo, ho = add_job(uid, c)
    jc, hc = add_job(uid, c, status="closed")
    jd, hd = add_job(uid, c, status="duplicate")
    add_match(uid, jo, 1, "ok", ho)
    add_match(uid, jc, 1, "ok", hc)
    add_match(uid, jd, 1, "ok", hd)
    jc2, hc2 = add_job(uid, c, status="closed")
    add_match(uid, jc2, 1, "pendente", hc2)
    b = as_user(uid).get("/overview").json()
    assert b["matches"] == {"scored": 1, "pending": 0}
    assert b["matches"]["scored"] <= b["jobs"]["open"]


@pytest.mark.db
def test_overview_never_500_when_part_missing(env, as_user):
    uid = env[0]()
    env[3](uid, version=1)  # profile with a chunk but no embedding/sessions
    r = as_user(uid).get("/overview")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["interview"]["state"] == "none"
    assert b["matches"] == {"scored": 0, "pending": 0}
    assert b["profile"]["vectorized"] is False


@pytest.mark.db
def test_overview_has_no_job_text(env, as_user):
    new_user, add_company, add_job, add_profile, add_match, _ = env
    uid = new_user()
    add_profile(uid, data={"basics": {"name": "Ana", "email": "a@x.test"}})
    c = add_company(uid)
    j, h = add_job(uid, c)
    add_match(uid, j, 1, "ok", h)
    r = as_user(uid).get("/overview")
    b = r.json()
    assert set(b) == CONTRACT_KEYS
    assert set(b["profile"]) == {"confirmed", "version", "vectorized", "provisional", "name", "email"}
    assert set(b["interview"]) == {"state"}
    assert set(b["companies"]) == {"total", "active", "auto"}
    assert set(b["jobs"]) == {"open", "outside_filters"}
    assert set(b["matches"]) == {"scored", "pending"}
    assert "SECRET" not in r.text
