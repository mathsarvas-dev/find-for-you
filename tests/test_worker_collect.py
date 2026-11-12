import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, User
from rumo_core.sources.base import (
    RawJob,
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)
from rumo_core.sources.greenhouse import GreenhouseSource
from worker import collect_jobs as cj
from worker.collect_jobs import collect_companies

pytestmark = pytest.mark.db

FIX = Path(__file__).parent / "fixtures" / "greenhouse"
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def raw(eid, title=None):
    return RawJob(
        external_id=str(eid), title=title or f"Engineer {eid}",
        url=f"https://example.test/jobs/{eid}", location_text="Remote, Brazil",
        description_html="<p>Python backend.</p>", department="Eng",
        posted_at=None, raw={"id": eid},
    )


class FakeSource:
    name = "fake"

    def __init__(self, results=None):
        # identifier -> list[RawJob] | Exception | callable
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


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"worker-collect-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def add_company(uid, identifier, **kw):
    with SessionLocal() as s:
        c = Company(user_id=uid, source="greenhouse", identifier=identifier,
                    name=identifier.title(), **kw)
        s.add(c)
        s.commit()
        return c.id


def get_company(cid):
    with SessionLocal() as s:
        return s.get(Company, cid)


def job_rows(cid):
    with SessionLocal() as s:
        return list(s.scalars(select(Job).where(Job.company_id == cid).order_by(Job.id)))


async def collect(uid, source, *, now=NOW, sleep=None, interval=6, delay=1.5, **kw):
    return await collect_companies(
        SessionLocal, source_getter=lambda name: source, user_id=uid,
        clock=lambda: now, sleep=sleep or Sleeps(), interval_hours=interval,
        delay_seconds=delay, **kw,
    )


async def test_success_updates_health_and_schedules_next(uid):
    cid = add_company(uid, "acme", consecutive_failures=3, last_error="old")
    src = FakeSource({"acme": [raw(1), raw(2)]})
    assert await collect(uid, src) == 1
    c = get_company(cid)
    assert c.last_success_at == NOW and c.last_run_at == NOW
    assert c.last_error is None and c.consecutive_failures == 0
    assert c.next_run_at == NOW + timedelta(hours=6)
    assert c.open_jobs == 2
    assert [j.external_id for j in job_rows(cid)] == ["1", "2"]


async def test_failure_records_error_and_exponential_backoff_capped(uid):
    cid = add_company(uid, "acme")
    src = FakeSource({"acme": SourceUnavailable("timeout")})
    expected = [15, 30, 60, 120, 240, 360, 360]  # minutes, capped at interval (6 h)
    now = NOW
    for i, minutes in enumerate(expected, start=1):
        assert await collect(uid, src, now=now) == 1
        c = get_company(cid)
        assert c.consecutive_failures == i
        assert c.last_run_at == now and c.last_success_at is None
        assert c.last_error == "Fonte indisponível no momento."
        assert c.next_run_at == now + timedelta(minutes=minutes)
        now = c.next_run_at
    # success resets the failure count
    src.results["acme"] = [raw(1)]
    await collect(uid, src, now=now)
    c = get_company(cid)
    assert c.consecutive_failures == 0 and c.last_error is None


async def test_not_found_marks_error_message_pt_br(uid):
    cid = add_company(uid, "acme")
    await collect(uid, FakeSource({"acme": SourceNotFound("404")}))
    assert get_company(cid).last_error == "Quadro não encontrado no Greenhouse."


@pytest.mark.parametrize("exc,msg", [
    (SourceBadResponse("x"), "Resposta inesperada da fonte."),
    (RuntimeError("x"), "Falha inesperada na coleta."),
])
async def test_other_error_messages(uid, exc, msg):
    cid = add_company(uid, "acme")
    await collect(uid, FakeSource({"acme": exc}))
    c = get_company(cid)
    assert c.last_error == msg and c.consecutive_failures == 1


async def test_unknown_source_is_a_failure(uid):
    cid = add_company(uid, "acme")

    def getter(name):
        raise KeyError(name)

    n = await collect_companies(
        SessionLocal, source_getter=getter, user_id=uid, clock=lambda: NOW,
        sleep=Sleeps(), interval_hours=6, delay_seconds=0,
    )
    c = get_company(cid)
    assert n == 1 and c.last_error == "Fonte desconhecida."
    assert c.consecutive_failures == 1 and c.next_run_at == NOW + timedelta(minutes=15)


async def test_suspicious_empty_result_keeps_jobs_and_records_failure(uid):
    cid = add_company(uid, "acme")
    src = FakeSource({"acme": [raw(i) for i in range(6)]})
    await collect(uid, src)
    src.results["acme"] = []
    later = NOW + timedelta(hours=6)
    await collect(uid, src, now=later)
    c = get_company(cid)
    assert c.last_error == "A fonte devolveu uma lista vazia de vagas; mantive as vagas abertas."
    assert c.consecutive_failures == 1 and c.open_jobs == 6
    assert c.last_success_at == NOW and c.last_run_at == later
    assert all(j.status == "open" for j in job_rows(cid))


async def test_one_company_failure_does_not_stop_others(uid):
    a = add_company(uid, "a")
    b = add_company(uid, "b")
    c3 = add_company(uid, "c")
    src = FakeSource({"a": [raw(1)], "b": RuntimeError("boom"), "c": [raw(2)]})
    sleeps = Sleeps()
    assert await collect(uid, src, sleep=sleeps, delay=2.5) == 3
    assert get_company(a).open_jobs == 1 and get_company(c3).open_jobs == 1
    assert get_company(b).last_error == "Falha inesperada na coleta."
    assert sorted(src.calls) == ["a", "b", "c"]
    assert sleeps.calls == [2.5, 2.5]  # between companies, not after the last


async def test_max_companies_limits_one_run(uid):
    ids = [add_company(uid, f"c{i}") for i in range(5)]
    src = FakeSource()
    sleeps = Sleeps()
    assert await collect(uid, src, sleep=sleeps, max_companies=2) == 2
    assert src.calls == ["c0", "c1"] and sleeps.calls == [1.5]
    due = [c for c in ids if get_company(c).last_run_at is None]
    assert len(due) == 3
    assert await collect(uid, src, max_companies=2) == 2
    assert await collect(uid, src, max_companies=2) == 1


async def test_time_budget_stops_claiming(uid):
    for i in range(4):
        add_company(uid, f"c{i}")
    t = {"now": NOW}

    async def slow():
        t["now"] += timedelta(seconds=40)  # each fetch "takes" 40 s
        return []

    src = FakeSource({f"c{i}": slow for i in range(4)})
    n = await collect_companies(
        SessionLocal, source_getter=lambda name: src, user_id=uid,
        clock=lambda: t["now"], sleep=Sleeps(), interval_hours=6, delay_seconds=0,
        max_seconds=60,
    )
    # 0 s: claim c0 -> 40 s: claim c1 (budget not reached) -> 80 s: stop
    assert n == 2 and src.calls == ["c0", "c1"]


async def test_most_overdue_first(uid):
    late = add_company(uid, "late", next_run_at=NOW - timedelta(hours=1))
    never = add_company(uid, "never")
    later = add_company(uid, "later", next_run_at=NOW - timedelta(hours=3))
    src = FakeSource()
    await collect(uid, src)
    assert src.calls == ["never", "later", "late"]
    assert all(get_company(c).last_run_at == NOW for c in (late, never, later))


async def test_inactive_and_not_due_companies_skipped(uid):
    add_company(uid, "off", active=False)
    add_company(uid, "future", next_run_at=NOW + timedelta(minutes=1))
    due = add_company(uid, "due", next_run_at=NOW)
    src = FakeSource()
    assert await collect(uid, src) == 1
    assert src.calls == ["due"]
    assert get_company(due).last_run_at == NOW


async def test_user_scope_filter(uid):
    with SessionLocal() as s:
        other = User(email=f"worker-collect-other-{uuid.uuid4().hex}@example.test")
        s.add(other)
        s.commit()
        other_id = other.id
    try:
        add_company(other_id, "theirs")
        add_company(uid, "mine")
        src = FakeSource()
        assert await collect(uid, src) == 1
        assert src.calls == ["mine"]
    finally:
        with SessionLocal() as s:
            s.execute(delete(Company).where(Company.user_id == other_id))
            s.execute(delete(User).where(User.id == other_id))
            s.commit()


async def test_skip_locked_company(uid):
    locked = add_company(uid, "locked")
    free = add_company(uid, "free")
    src = FakeSource()
    with SessionLocal() as holder:
        holder.execute(
            select(Company).where(Company.id == locked).with_for_update()
        ).scalar_one()
        assert await collect(uid, src) == 1
        holder.rollback()
    assert src.calls == ["free"]
    assert get_company(locked).last_run_at is None
    assert get_company(free).last_run_at == NOW


def test_lease_claim_prevents_second_claim(uid):
    cid = add_company(uid, "acme")
    claimed = cj._claim_company(SessionLocal, uid, NOW)
    assert claimed is not None and claimed.company_id == cid
    assert get_company(cid).next_run_at == NOW + cj.CLAIM_LEASE
    # another worker/tick at the same moment cannot take it while the fetch runs
    assert cj._claim_company(SessionLocal, uid, NOW) is None
    assert cj._claim_company(SessionLocal, uid, NOW + cj.CLAIM_LEASE) is not None


async def test_lease_is_held_while_fetching(uid):
    cid = add_company(uid, "acme")
    seen = {}

    async def during_fetch():
        seen["next"] = get_company(cid).next_run_at
        seen["second"] = cj._claim_company(SessionLocal, uid, NOW)
        return [raw(1)]

    await collect(uid, FakeSource({"acme": during_fetch}))
    assert seen["next"] == NOW + cj.CLAIM_LEASE and seen["second"] is None


async def test_cancelled_midway_leaves_no_half_state(uid):
    a = add_company(uid, "a", next_run_at=NOW - timedelta(hours=2))
    b = add_company(uid, "b", next_run_at=NOW - timedelta(hours=1), last_error="old")

    async def cancelled():
        raise asyncio.CancelledError()

    src = FakeSource({"a": [raw(1)], "b": cancelled})
    with pytest.raises(asyncio.CancelledError):
        await collect(uid, src)
    ca, cb = get_company(a), get_company(b)
    assert ca.last_success_at == NOW and ca.open_jobs == 1
    # b keeps only the lease: retried after it; no jobs, no failure recorded
    assert cb.next_run_at == NOW + cj.CLAIM_LEASE
    assert cb.last_run_at is None and cb.consecutive_failures == 0
    assert cb.last_error == "old" and job_rows(b) == []


async def test_error_message_has_no_response_body(uid, caplog):
    cid = add_company(uid, "acme")
    body = "<html>segredo-do-corpo-xyz</html>"
    with caplog.at_level("INFO"):
        await collect(uid, FakeSource({"acme": SourceBadResponse(body)}))
    c = get_company(cid)
    assert "segredo-do-corpo-xyz" not in (c.last_error or "")
    assert "segredo-do-corpo-xyz" not in caplog.text
    assert any("SourceBadResponse" in r.getMessage() and str(cid) in r.getMessage()
               for r in caplog.records)


async def test_real_fixture_end_to_end(uid):
    data = (FIX / "gitlab.json").read_bytes()
    count = len(json.loads(data)["jobs"])

    def handler(request):
        return httpx.Response(200, content=data, headers={"content-type": "application/json"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src = GreenhouseSource(client, max_bytes=5_000_000)
    try:
        cid = add_company(uid, "gitlab")
        assert await collect(uid, src) == 1
        rows = job_rows(cid)
        assert len(rows) == count
        c = get_company(cid)
        assert c.last_error is None and c.open_jobs == sum(r.status == "open" for r in rows)
        for r in rows:
            assert r.status in ("open", "duplicate")
            assert r.work_model == "remote"
            assert r.description and "<" not in r.description[:200]
            assert r.title and r.url.startswith("http") and r.raw
            assert r.language in ("en", "unknown") and r.department
        assert {r.country for r in rows} <= {None, "US", "CA", "PL", "GB"}
        with SessionLocal() as s:
            s.execute(
                Job.__table__.update().where(Job.company_id == cid)
                .values(embedding=[0.5] * 1024, model_version="m1")
            )
            s.commit()
        before = {r.external_id: (r.id, r.status, r.content_hash) for r in rows}
        later = NOW + timedelta(hours=6)
        assert await collect(uid, src, now=later) == 1
        rows2 = job_rows(cid)
        assert {r.external_id: (r.id, r.status, r.content_hash) for r in rows2} == before
        assert all(r.model_version == "m1" and r.embedding is not None for r in rows2)
        assert all(r.last_seen_at == later for r in rows2)
        assert get_company(cid).open_jobs == c.open_jobs
    finally:
        await src.aclose()
