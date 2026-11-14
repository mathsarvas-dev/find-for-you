import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

import api.chat_filters as cf
from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMRateLimited
from rumo_core.matching.chat_filters import EMPTY_NOTE, _RawParsed
from rumo_core.models import Company, Job, SearchProfile, User


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeLLM:
    def __init__(self, results=None, exc=None):
        self.results = list(results or [])
        self.exc = exc
        self.calls = []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        if self.exc is not None:
            raise self.exc
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


GOOD = _RawParsed(filters={"title_exclude": ["plantão"]}, query_text=None, notes=[])


@pytest.fixture
def env():
    created: list[int] = []

    def new_user(dept=None):
        with SessionLocal() as s:
            u = User(email=f"api-cf-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            if dept:
                c = Company(user_id=u.id, source="greenhouse",
                            identifier=f"acme{u.id}", name="Acme")
                s.add(c)
                s.commit()
                s.add(Job(user_id=u.id, company_id=c.id, external_id="1", title="t",
                          url="https://example.test/1", description="d", raw={},
                          content_hash=f"h{u.id}", fingerprint=f"f{u.id}",
                          department=dept, status="open"))
                s.commit()
            return u.id

    yield new_user
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(SearchProfile).where(SearchProfile.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


@pytest.fixture
def setup():
    clock = FakeClock()
    limiter = cf.HourlyLimiter(clock=clock)
    app.dependency_overrides[cf.get_parse_limiter] = lambda: limiter

    def _as(uid, llm):
        app.dependency_overrides[current_user_id] = lambda: uid
        app.dependency_overrides[cf.get_filters_llm] = lambda: llm
        return TestClient(app)

    yield _as, clock
    app.dependency_overrides.clear()


def post(c, text="sem plantão"):
    return c.post("/filters/parse", json={"text": text})


@pytest.mark.db
def test_parse_returns_proposal_and_saves_nothing(env, setup):
    as_user, _ = setup
    uid = env()
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name="x", filters={"seniorities": ["senior"]},
                            is_default=True))
        s.commit()
        before = s.scalars(select(SearchProfile.filters).where(SearchProfile.user_id == uid)).all()
    r = post(as_user(uid, FakeLLM([GOOD])))
    assert r.status_code == 200, r.text
    assert r.json()["filters"]["title_exclude"] == ["plantão"]
    with SessionLocal() as s:
        after = s.scalars(select(SearchProfile.filters).where(SearchProfile.user_id == uid)).all()
    assert after == before


@pytest.mark.db
def test_parse_sends_current_filters_and_user_scope(env, setup):
    as_user, _ = setup
    uid, other = env("Engineering"), env("Secret Dept")
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name="x", filters={"seniorities": ["senior"]},
                            is_default=True))
        s.commit()
    llm = FakeLLM([GOOD])
    assert post(as_user(uid, llm)).status_code == 200
    content = " ".join(m["content"] for m in llm.calls[0])
    assert '"seniorities": ["senior"]' in content
    assert "Engineering" in content and "Secret Dept" not in content


@pytest.mark.db
def test_parse_hourly_limit_429(env, setup):
    as_user, clock = setup
    uid, other = env(), env()
    llm = FakeLLM([GOOD])
    c = as_user(uid, llm)
    for _ in range(20):
        assert post(c).status_code == 200
    r = post(c)
    assert r.status_code == 429
    assert r.json()["detail"] == cf.MSG_PARSE_LIMIT
    assert int(r.headers["Retry-After"]) > 0
    assert len(llm.calls) == 20
    assert post(as_user(other, llm)).status_code == 200
    clock.t += 3601
    assert post(as_user(uid, llm)).status_code == 200


@pytest.mark.db
def test_parse_invalid_json_422_after_retry(env, setup):
    as_user, _ = setup
    uid = env()
    bad = LLMInvalidResponse("x")
    llm = FakeLLM([bad, bad])
    r = post(as_user(uid, llm))
    assert r.status_code == 422 and r.json()["detail"] == cf.MSG_PARSE_INVALID
    assert len(llm.calls) == 2


@pytest.mark.db
def test_parse_llm_busy_503_and_failures_count(env, setup):
    as_user, _ = setup
    uid = env()
    for exc in (LLMRateLimited("x"), LLMError("secret phrase")):
        r = post(as_user(uid, FakeLLM(exc=exc)))
        assert r.status_code == 503 and r.json()["detail"] == cf.MSG_LLM_BUSY
        assert "secret" not in r.text
    c = as_user(uid, FakeLLM(exc=LLMError("x")))
    for _ in range(18):
        assert post(c).status_code == 503
    assert post(c).status_code == 429  # failures were counted


@pytest.mark.db
def test_parse_without_llm_503(env, setup):
    as_user, _ = setup
    r = post(as_user(env(), None))
    assert r.status_code == 503 and r.json()["detail"] == cf.MSG_NO_LLM


@pytest.mark.db
def test_parse_text_limits_and_bad_chars(env, setup):
    as_user, _ = setup
    uid = env()
    llm = FakeLLM([GOOD])
    c = as_user(uid, llm)
    assert post(c, "x" * 301).status_code == 422
    r = post(c, "   ")
    assert r.status_code == 200 and r.json()["notes"] == [EMPTY_NOTE]
    r = post(c, "remoto‮evil")
    assert r.status_code == 422 and r.json()["detail"] == cf.MSG_BAD_TEXT
    assert llm.calls == []
    for _ in range(20):  # nothing above consumed the limit
        assert post(c).status_code == 200


@pytest.mark.db
def test_parse_invalid_saved_filters_409(env, setup):
    as_user, _ = setup
    uid = env()
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name="x", filters={"work_models": ["space"]},
                            is_default=True))
        s.commit()
    r = post(as_user(uid, FakeLLM([GOOD])))
    assert r.status_code == 409
    from api.matching import MSG_BAD_SAVED_FILTERS
    assert r.json()["detail"] == MSG_BAD_SAVED_FILTERS


def test_limiter_bounded_memory():
    clock = FakeClock()
    lim = cf.HourlyLimiter(max_calls=2, window=10, clock=clock)
    for uid in range(cf.MAX_TRACKED_USERS):
        assert lim.acquire(uid)
    clock.t += 11
    assert lim.acquire(10**6)
    assert len(lim._stamps) == 1
