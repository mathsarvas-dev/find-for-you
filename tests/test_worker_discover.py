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


def add_cand(uid, token, *, name_hint=None, origin_kind="catalog", **kw):
    with SessionLocal() as s:
        c = DiscoveryCandidate(user_id=uid, source="greenhouse", token=token,
                               name_hint=name_hint, origin_kind=origin_kind, **kw)
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
    assert names[names.index("collect_companies") + 1] == "collect_feeds"
    assert names[names.index("collect_feeds") + 1] == "discover_companies"
    assert reg._jobs["discover_companies"][1] == 60


@pytest.mark.db
async def test_origin_priority_target_then_profile_before_catalog(uid):
    cat = [add_cand(uid, f"cat{i}") for i in range(3)]  # inserted first
    tgt = add_cand(uid, "mytarget", origin_kind="target")
    prof = add_cand(uid, "myemployer", origin_kind="profile")
    with SessionLocal() as s:  # backed by real sources, or refresh retires them
        s.add(UserPreference(user_id=uid, target_companies=["mytarget"]))
        s.add(Profile(user_id=uid, version=1, content_hash="h",
                      data={"experiences": [{"company": "myemployer"}]}))
        s.commit()
    src = FakeSource({t: [raw(1)] for t in
                      ("cat0", "cat1", "cat2", "mytarget", "myemployer")})
    assert await run(uid, src, max_auto=2) == 2
    assert src.calls == ["mytarget", "myemployer"]
    assert cand(tgt).state == cand(prof).state == "valid"
    assert all(cand(i).state == "pending" for i in cat)
    assert {c.identifier for c in companies(uid)} == {"mytarget", "myemployer"}


@pytest.fixture
def three_users():
    ids = [_new_user() for _ in range(3)]
    yield ids
    for u in ids:
        _purge(u)


def _only(monkeypatch, ids):
    real = dmod._enabled_users

    def enabled(session_factory, user_id):
        return sorted(u for i in ids for u in real(session_factory, i))

    monkeypatch.setattr(dmod, "_enabled_users", enabled)


async def run_all(source, *, batch, now=NOW):
    return await discover_companies(
        SessionLocal, batch=batch, max_seconds=60, max_auto=100, delay=0,
        max_attempts=3, source_getter=lambda name: source,
        clock=lambda: 0.0, sleep=Sleeps(), utcnow=lambda: now,
    )


def _owners(src, ids):
    return [int(t.split("x")[0][1:]) for t in src.calls]


@pytest.mark.db
async def test_round_robin_fairness_across_users(three_users, monkeypatch):
    _only(monkeypatch, three_users)
    for u in three_users:
        for i in range(6):
            add_cand(u, f"u{u}x{i}")
    src = FakeSource()  # every board empty -> invalid
    # one tick, batch 6, 3 users -> 2 each (not 6 for the lowest id)
    assert await run_all(src, batch=6) == 6
    owners = _owners(src, three_users)
    assert sorted(owners) == sorted(three_users * 2)
    # batch 1: the starting user rotates across ticks
    firsts = []
    for _ in range(4):
        src.calls.clear()
        assert await run_all(src, batch=1) == 1
        firsts += _owners(src, three_users)
    a, b, c = three_users
    # previous tick started at a, so the next ones start at b, c, a, b
    assert firsts == [b, c, a, b]


@pytest.mark.db
async def test_round_robin_gives_leftover_budget_to_busy_users(three_users, monkeypatch):
    _only(monkeypatch, three_users)
    a, b, c = three_users
    for i in range(5):
        add_cand(a, f"u{a}x{i}")
    add_cand(b, f"u{b}x0")  # c has nothing
    src = FakeSource()
    assert await run_all(src, batch=6) == 6
    owners = _owners(src, three_users)
    assert owners.count(b) == 1 and owners.count(a) == 5


@pytest.mark.db
async def test_recheck_transient_failures_never_make_it_permanent(uid):
    cid = add_cand(uid, "quiet")
    src = FakeSource({"quiet": []})
    await run(uid, src)  # empty -> invalid, +30 d
    src.results["quiet"] = SourceUnavailable("down")
    now = NOW + timedelta(days=30)
    for attempt in (1, 2):
        assert await run(uid, src, now=now) == 1
        c = cand(cid)
        assert c.state == "invalid" and c.attempts == attempt
        assert c.retry_after == now + timedelta(hours=2 ** (attempt - 1))
        now = c.retry_after
    assert await run(uid, src, now=now) == 1
    c = cand(cid)
    # max attempts during a recheck: still invalid, but rechecked in 30 days
    assert c.state == "invalid" and c.attempts == 0
    assert c.retry_after == now + timedelta(days=30)
    assert c.last_error == "SourceUnavailable"
    src.results["quiet"] = [raw(1)]
    assert await run(uid, src, now=now + timedelta(days=30)) == 1
    assert cand(cid).state == "valid" and len(companies(uid)) == 1


@pytest.mark.db
async def test_on_conflict_branch_company_inserted_after_locked_check(uid, monkeypatch):
    cid = add_cand(uid, "racy")
    real = dmod._existing_company_id
    seen = {"n": 0, "company": None}

    def racing(s, user_id, source, token):
        seen["n"] += 1
        if seen["n"] == 2:  # the check under the user lock in _record_valid
            # another transaction commits the same company right now (it must
            # not block on the user-row lock: FOR NO KEY UPDATE vs FK KEY SHARE)
            seen["company"] = add_company(uid, "racy", active=False, name="Theirs")
            return None
        return real(s, user_id, source, token)

    monkeypatch.setattr(dmod, "_existing_company_id", racing)
    src = FakeSource({"racy": [raw(1)]})
    assert await run(uid, src) == 1
    assert seen["n"] == 3  # pre-fetch, locked check, after the empty insert
    c = cand(cid)
    assert c.state == "known" and c.company_id == seen["company"]
    [co] = companies(uid)
    assert co.id == seen["company"] and co.active is False
    assert co.name == "Theirs" and co.origin == "manual" and co.next_run_at is None


@pytest.mark.db
async def test_removed_target_is_never_validated(uid):
    gone = add_cand(uid, "gonetarget", origin_kind="target")
    with SessionLocal() as s:
        s.add(UserPreference(user_id=uid, target_companies=[]))
        s.commit()
    src = FakeSource({"gonetarget": [raw(1)]})
    assert await run(uid, src) == 0
    assert src.calls == []
    with SessionLocal() as s:
        assert s.get(DiscoveryCandidate, gone) is None
    assert companies(uid) == []
