import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.discovery import candidates as cmod
from rumo_core.discovery.catalog import CatalogEntry
from rumo_core.models import (
    Company, DiscoveryCandidate, Job, Profile, User, UserPreference,
)
from rumo_core.sources.base import (
    RawJob, SourceBadResponse, SourceNotFound, SourceUnavailable,
)
from worker import discover_companies as dmod
from worker.discover_companies import discover_companies

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def raw(eid, company_name=None):
    item = {"id": eid}
    if company_name is not None:
        item["company_name"] = company_name
    return RawJob(
        external_id=str(eid), title=f"Engineer {eid}",
        url=f"https://example.test/jobs/{eid}", location_text="Remote",
        description_html="<p>x</p>", department=None, posted_at=None, raw=item,
    )


class FakeSource:
    name = "greenhouse"

    def __init__(self, results=None):
        # token -> list[RawJob] | Exception | async callable
        self.results = results or {}
        self.calls: list[str] = []

    async def fetch_jobs(self, identifier):
        self.calls.append(identifier)
        r = self.results.get(identifier, [])
        if callable(r):
            r = await r()
        if isinstance(r, BaseException):
            raise r
        return list(r)

    def normalize_identifier(self, text):
        return text.lower()

    async def aclose(self):
        pass


class Sleeps:
    def __init__(self):
        self.calls = []

    async def __call__(self, s):
        self.calls.append(s)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    dmod._reset_state()
    monkeypatch.setattr(cmod, "load_catalog", lambda *a, **k: [])
    yield
    dmod._reset_state()


def _purge(uid):
    with SessionLocal() as s:
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid))
        s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
        s.execute(delete(Job).where(Job.user_id == uid))
        s.execute(delete(Company).where(Company.user_id == uid))
        s.execute(delete(Profile).where(Profile.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _new_user():
    with SessionLocal() as s:
        u = User(email=f"worker-discover-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        return u.id


@pytest.fixture
def uid():
    user_id = _new_user()
    yield user_id
    _purge(user_id)


@pytest.fixture
def uid2():
    user_id = _new_user()
    yield user_id
    _purge(user_id)


def add_cand(uid, token, *, name_hint=None, **kw):
    with SessionLocal() as s:
        c = DiscoveryCandidate(user_id=uid, source="greenhouse", token=token,
                               name_hint=name_hint, origin_kind="catalog", **kw)
        s.add(c)
        s.commit()
        return c.id


def add_company(uid, identifier, **kw):
    kw.setdefault("name", identifier.title())
    with SessionLocal() as s:
        c = Company(user_id=uid, source="greenhouse", identifier=identifier, **kw)
        s.add(c)
        s.commit()
        return c.id


def cand(cid):
    with SessionLocal() as s:
        return s.get(DiscoveryCandidate, cid)


def companies(uid):
    with SessionLocal() as s:
        return list(s.scalars(
            select(Company).where(Company.user_id == uid).order_by(Company.id)
        ))


async def run(uid, source, *, now=NOW, batch=10, max_seconds=60, max_auto=100,
              delay=1.5, max_attempts=3, sleep=None, clock=None, getter=None):
    return await discover_companies(
        SessionLocal, batch=batch, max_seconds=max_seconds, max_auto=max_auto,
        delay=delay, max_attempts=max_attempts, user_id=uid,
        source_getter=getter or (lambda name: source),
        clock=clock or (lambda: 0.0), sleep=sleep or Sleeps(),
        utcnow=lambda: now,
    )


@pytest.mark.db
async def test_valid_creates_auto_company_with_immediate_run(uid, caplog):
    caplog.set_level(logging.DEBUG)
    cid = add_cand(uid, "acmewidgets", name_hint="Acme Widgets Inc")
    src = FakeSource({"acmewidgets": [raw(1), raw(2)]})
    assert await run(uid, src) == 1
    assert src.calls == ["acmewidgets"]
    [co] = companies(uid)
    assert co.origin == "auto" and co.active is True
    assert co.next_run_at == NOW
    assert co.identifier == "acmewidgets" and co.name == "Acme Widgets Inc"
    c = cand(cid)
    assert c.state == "valid" and c.company_id == co.id
    assert c.checked_at == NOW and c.retry_after is None and c.last_error is None
    # never log names or tokens
    assert "Acme" not in caplog.text and "acmewidgets" not in caplog.text


@pytest.mark.db
async def test_valid_prefers_source_name_then_token(uid):
    a = add_cand(uid, "alpha", name_hint="Hint")
    b = add_cand(uid, "beta")
    src = FakeSource({"alpha": [raw(1, "Alpha Corp")], "beta": [raw(2)]})
    assert await run(uid, src) == 2
    names = {c.identifier: c.name for c in companies(uid)}
    assert names == {"alpha": "Alpha Corp", "beta": "beta"}
    assert cand(a).state == cand(b).state == "valid"


@pytest.mark.db
async def test_404_marks_invalid(uid, caplog):
    caplog.set_level(logging.DEBUG)
    cid = add_cand(uid, "ghost", name_hint="Ghost Co")
    src = FakeSource({"ghost": SourceNotFound("board 'ghost' not found")})
    assert await run(uid, src) == 1
    c = cand(cid)
    assert c.state == "invalid" and c.retry_after is None
    assert c.last_error == "SourceNotFound" and c.checked_at == NOW
    assert companies(uid) == []
    assert "ghost" not in caplog.text.lower()
    # never retried
    assert await run(uid, src, now=NOW + timedelta(days=400)) == 0


@pytest.mark.db
async def test_zero_jobs_invalid_retry_30d(uid):
    cid = add_cand(uid, "quiet")
    src = FakeSource({"quiet": []})
    assert await run(uid, src) == 1
    c = cand(cid)
    assert c.state == "invalid" and c.retry_after == NOW + timedelta(days=30)
    assert companies(uid) == []
    # not before the 30 days
    assert await run(uid, src, now=NOW + timedelta(days=29)) == 0
    # rechecked after them; now hiring -> valid
    src.results["quiet"] = [raw(1)]
    later = NOW + timedelta(days=30)
    assert await run(uid, src, now=later) == 1
    c = cand(cid)
    assert c.state == "valid" and len(companies(uid)) == 1


@pytest.mark.db
@pytest.mark.parametrize("exc", [
    SourceUnavailable("HTTP 429"), SourceBadResponse("bad json"), TimeoutError(),
])
async def test_transient_does_not_burn_attempt_definitively(uid, exc):
    cid = add_cand(uid, "flaky")
    src = FakeSource({"flaky": exc})
    now = NOW
    for attempt, wait in [(1, timedelta(hours=1)), (2, timedelta(hours=2))]:
        assert await run(uid, src, now=now) == 1
        c = cand(cid)
        assert c.state == "pending" and c.attempts == attempt
        assert c.retry_after == now + wait
        assert c.last_error == type(exc).__name__
        # not retried before retry_after
        assert await run(uid, src, now=now + wait - timedelta(seconds=1)) == 0
        now = c.retry_after
    assert await run(uid, src, now=now) == 1
    c = cand(cid)
    assert c.state == "invalid" and c.attempts == 3
    assert c.last_error == type(exc).__name__ and c.retry_after is None
    assert companies(uid) == [] and len(src.calls) == 3


@pytest.mark.db
async def test_transient_then_success_creates_company(uid):
    cid = add_cand(uid, "flaky")
    src = FakeSource({"flaky": SourceUnavailable("x")})
    await run(uid, src)
    src.results["flaky"] = [raw(1)]
    assert await run(uid, src, now=NOW + timedelta(hours=1)) == 1
    assert cand(cid).state == "valid" and len(companies(uid)) == 1


@pytest.mark.db
async def test_cap_stops_creation(uid):
    add_company(uid, "auto1", origin="auto")
    add_company(uid, "auto2", origin="auto")
    add_company(uid, "paused", origin="auto", active=False)  # does not count
    add_company(uid, "manual1")  # manual does not count
    ids = [add_cand(uid, t) for t in ("c1", "c2", "c3")]
    src = FakeSource({t: [raw(1)] for t in ("c1", "c2", "c3")})
    # cap already reached: no network at all, nothing created
    assert await run(uid, src, max_auto=2) == 0
    assert src.calls == [] and len(companies(uid)) == 4
    # one slot left: exactly one created, the rest stay pending
    assert await run(uid, src, max_auto=3) == 1
    assert len(src.calls) == 1
    states = sorted(cand(i).state for i in ids)
    assert states == ["pending", "pending", "valid"]
    assert sum(1 for c in companies(uid) if c.origin == "auto" and c.active) == 3


@pytest.mark.db
async def test_cap_reached_during_fetch_keeps_candidate_pending(uid):
    cid = add_cand(uid, "late")

    async def fill_cap():
        add_company(uid, "other", origin="auto")  # another worker took the slot
        return [raw(1)]

    src = FakeSource({"late": fill_cap})
    assert await run(uid, src, max_auto=1) == 1
    c = cand(cid)
    assert c.state == "pending" and c.retry_after is None and c.company_id is None
    assert [co.identifier for co in companies(uid)] == ["other"]


@pytest.mark.db
async def test_disabled_user_skipped(uid, monkeypatch):
    monkeypatch.setattr(cmod, "load_catalog",
                        lambda *a, **k: [CatalogEntry("alpha", "Alpha")])
    with SessionLocal() as s:
        s.add(UserPreference(user_id=uid, discovery_enabled=False))
        s.commit()
    cid = add_cand(uid, "acme")
    src = FakeSource({"acme": [raw(1)]})
    assert await run(uid, src) == 0
    assert src.calls == [] and companies(uid) == []
    assert cand(cid).state == "pending"
    with SessionLocal() as s:  # refresh did not run either
        toks = s.scalars(select(DiscoveryCandidate.token)
                         .where(DiscoveryCandidate.user_id == uid)).all()
    assert toks == ["acme"]


@pytest.mark.db
async def test_missing_preference_row_means_enabled_and_refreshes(uid, monkeypatch):
    monkeypatch.setattr(cmod, "load_catalog",
                        lambda *a, **k: [CatalogEntry("alpha", "Alpha")])
    src = FakeSource({"alpha": [raw(1)]})
    assert await run(uid, src) == 1
    [co] = companies(uid)
    assert co.identifier == "alpha" and co.name == "Alpha"


@pytest.mark.db
async def test_refresh_at_most_hourly(uid, monkeypatch):
    calls = []
    real = dmod.refresh_candidates

    def counting(session, user_id):
        calls.append(user_id)
        return real(session, user_id)

    monkeypatch.setattr(dmod, "refresh_candidates", counting)
    src = FakeSource()
    t = [0.0]
    for at in (0.0, 1000.0, 3599.0):
        t[0] = at
        await run(uid, src, clock=lambda: t[0])
    assert calls == [uid]
    t[0] = 3600.0
    await run(uid, src, clock=lambda: t[0])
    assert calls == [uid, uid]
    dmod._reset_state()
    await run(uid, src, clock=lambda: t[0])
    assert len(calls) == 3


@pytest.mark.db
async def test_paused_or_existing_company_never_recreated(uid):
    # existing paused company: refresh marks the candidate known, no network
    paused = add_company(uid, "acme", active=False, name="My Acme")
    c1 = add_cand(uid, "acme")
    src = FakeSource({"acme": [raw(1)]})
    await run(uid, src)
    assert src.calls == []
    c = cand(c1)
    assert c.state == "known" and c.company_id == paused
    [co] = companies(uid)
    assert co.active is False and co.name == "My Acme" and co.origin == "manual"


@pytest.mark.db
async def test_company_created_during_fetch_becomes_known(uid):
    c1 = add_cand(uid, "racy")
    created = {}

    async def user_adds_it_meanwhile():
        created["id"] = add_company(uid, "racy", active=False, name="Mine",
                                    consecutive_failures=2)
        return [raw(1)]

    src = FakeSource({"racy": user_adds_it_meanwhile})
    assert await run(uid, src) == 1
    c = cand(c1)
    assert c.state == "known" and c.company_id == created["id"]
    [co] = companies(uid)
    assert co.active is False and co.name == "Mine" and co.origin == "manual"
    assert co.consecutive_failures == 2 and co.next_run_at is None


@pytest.mark.db
async def test_respects_batch_and_max_seconds(uid):
    tokens = [f"t{i}" for i in range(5)]
    for t in tokens:
        add_cand(uid, t)
    src = FakeSource({t: [] for t in tokens})
    sleeps = Sleeps()
    assert await run(uid, src, batch=2, delay=1.5, sleep=sleeps) == 2
    assert len(src.calls) == 2 and sleeps.calls == [1.5]

    # every fetch takes 10 "seconds"; stop once 15 s have elapsed
    t = [100.0]

    async def slow():
        t[0] += 10
        return []

    src2 = FakeSource({tok: slow for tok in tokens})
    assert await run(uid, src2, batch=10, max_seconds=15, clock=lambda: t[0]) == 2
    assert len(src2.calls) == 2


@pytest.mark.db
async def test_two_users_isolated(uid, uid2):
    for i in range(3):
        add_company(uid2, f"auto{i}", origin="auto")
    a = add_cand(uid, "shared")
    b = add_cand(uid2, "shared")
    src = FakeSource({"shared": [raw(1)]})
    # uid2's companies do not count toward uid's cap; uid2 untouched
    assert await run(uid, src, max_auto=1) == 1
    assert cand(a).state == "valid" and cand(b).state == "pending"
    assert [c.identifier for c in companies(uid)] == ["shared"]
    assert len(companies(uid2)) == 3
    # uid2 is at its cap
    assert await run(uid2, src, max_auto=3) == 0
    assert cand(b).state == "pending"


@pytest.mark.db
async def test_no_llm_or_other_hosts_used(uid, monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("real HTTP attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    asked = []
    add_cand(uid, "acme")
    src = FakeSource({"acme": [raw(1)]})

    def getter(name):
        asked.append(name)
        return src

    assert await run(uid, src, getter=getter) == 1
    assert asked == ["greenhouse"] and src.calls == ["acme"]
    import inspect

    text = inspect.getsource(dmod)
    assert "llm" not in text.lower().replace("no llm", "")
    assert "httpx" not in text


def test_job_registered_after_collect():
    from rumo_core.config import Settings
    from worker.main import build_discover_job, build_registry

    s = Settings(
        _env_file=None, database_url="postgresql+psycopg://u:p@localhost/x",
        discovery_batch=4, discovery_max_seconds=12, discovery_max_auto_companies=9,
        discovery_max_attempts=2, collect_delay_seconds=0.75,
    )
    sf = object()
    job = build_discover_job(s, sf)
    assert job.func is discover_companies and job.args == (sf,)
    assert job.keywords == {
        "batch": 4, "max_seconds": 12, "max_auto": 9, "delay": 0.75,
        "max_attempts": 2,
    }
    reg = build_registry(s, sf, None, object())
    names = list(reg._jobs)
    assert names[names.index("collect_companies") + 1] == "discover_companies"
    assert reg._jobs["discover_companies"][1] == 60
