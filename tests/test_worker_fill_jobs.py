import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from rumo_core.db import SessionLocal
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMRateLimited
from rumo_core.models import Company, Job, User
from rumo_core.sources.fill import FillResult
from worker import collect_jobs as cj
from worker.collect_jobs import fill_missing_job_fields

pytestmark = pytest.mark.db

PAY = "A fully remote role. The salary is USD 120,000 - 150,000 per year."
SECRET = "SECRET-POSTING-TEXT-XYZ"


class FakeLLM:
    def __init__(self, *results, exc=None, hook=None):
        self.results = list(results)
        self.exc = exc
        self.hook = hook
        self.calls: list[list[dict]] = []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        if self.hook:
            self.hook()
        if self.exc is not None:
            exc = self.exc
            if isinstance(exc, list):
                exc = exc[min(len(self.calls), len(exc)) - 1]
            if exc is not None:
                raise exc
        if self.results:
            return self.results.pop(0)
        return FillResult()


@pytest.fixture(autouse=True)
def _state():
    cj._reset_fill_state()
    yield
    cj._reset_fill_state()


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"fill-jobs-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def _job(uid, i=0, **kw):
    """Insert an open job; defaults need the LLM (work_model unknown)."""
    with SessionLocal() as s:
        c = s.execute(select(Company).where(Company.user_id == uid)).scalars().first()
        if c is None:
            c = Company(user_id=uid, source="fake", identifier=f"co{uid}", name="Acme")
            s.add(c)
            s.flush()
        fields = dict(
            user_id=uid, company_id=c.id, external_id=f"e{i}",
            url=f"https://example.test/{i}", title=f"Job {i}",
            description="Plain text.", raw={}, content_hash=f"h{i}",
            fingerprint=f"f{i}", status="open", work_model="unknown",
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
        )
        fields.update(kw)
        j = Job(**fields)
        s.add(j)
        s.commit()
        return j.id


def _get(job_id):
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


async def _run(uid, llm, batch=5, **kw):
    return await fill_missing_job_fields(SessionLocal, llm, batch=batch, user_id=uid, **kw)


async def test_fills_work_model_and_salary_and_marks_filled(uid):
    jid = _job(uid, description=PAY)
    llm = FakeLLM(FillResult(
        work_model="remote", salary_min=Decimal(120000), salary_max=Decimal(150000),
        salary_currency="USD", salary_period="year",
    ))
    assert await _run(uid, llm) == 1
    j = _get(jid)
    assert j.work_model == "remote" and j.salary_known
    assert (j.salary_min, j.salary_max, j.salary_currency, j.salary_period) == (
        Decimal(120000), Decimal(150000), "USD", "year")
    assert j.llm_filled_at is not None and j.fill_attempts == 0


async def test_success_with_nothing_validated_still_marks_filled(uid):
    jid = _job(uid, description=PAY)
    llm = FakeLLM(FillResult(
        salary_min=Decimal(999999), salary_currency="USD"))  # not in text
    await _run(uid, llm)
    j = _get(jid)
    assert j.llm_filled_at is not None and not j.salary_known and j.salary_min is None
    assert j.work_model == "unknown"


async def test_rule_values_not_overwritten(uid):
    jid = _job(uid, description=PAY, work_model="onsite", salary_known=True,
               salary_min=Decimal(1), salary_currency="EUR")
    llm = FakeLLM()
    await _run(uid, llm)
    assert llm.calls == []  # nothing missing: no call
    j = _get(jid)
    assert j.work_model == "onsite" and j.salary_min == 1 and j.salary_currency == "EUR"
    assert j.llm_filled_at is None  # not even a candidate

    # work model known by rules, salary missing: LLM must not override work_model
    jid2 = _job(uid, 1, description=PAY, work_model="hybrid")
    llm2 = FakeLLM(FillResult(work_model="remote"))
    await _run(uid, llm2)
    assert _get(jid2).work_model == "hybrid"


async def test_selection_skips_complete_jobs(uid):
    complete = _job(uid, 0, work_model="remote", salary_known=False)  # no pay context
    needs = _job(uid, 1)
    closed = _job(uid, 2, status="closed")
    done = _job(uid, 3, llm_filled_at=datetime(2026, 1, 1, tzinfo=UTC))
    maxed = _job(uid, 4, fill_attempts=3)
    llm = FakeLLM()
    assert await _run(uid, llm) == 1
    assert len(llm.calls) == 1
    assert _get(needs).llm_filled_at is not None
    assert _get(complete).llm_filled_at is not None  # settled without the LLM
    for other in (closed, maxed):
        assert _get(other).llm_filled_at is None
    assert _get(done).fill_attempts == 0


async def test_pay_context_job_selected_when_salary_unknown(uid):
    jid = _job(uid, work_model="remote", description=PAY)
    llm = FakeLLM()
    assert await _run(uid, llm) == 1
    assert _get(jid).llm_filled_at is not None


async def test_window_settles_non_needing_jobs_without_llm_call(uid):
    # newest 10*batch rows need nothing; older one needs the LLM
    needs = _job(uid, 0)
    for i in range(1, 9):
        _job(uid, i, work_model="remote")
    llm = FakeLLM()
    assert await _run(uid, llm, batch=1) == 1
    assert len(llm.calls) == 1
    assert _get(needs).llm_filled_at is not None
    with SessionLocal() as s:
        left = s.scalar(select(Job.id).where(
            Job.user_id == uid, Job.llm_filled_at.is_(None)).limit(1))
    assert left is None  # no endless re-selection


async def test_batch_limit_and_newest_first(uid):
    ids = [_job(uid, i) for i in range(4)]
    llm = FakeLLM()
    assert await _run(uid, llm, batch=2) == 2
    assert _get(ids[3]).llm_filled_at is not None and _get(ids[2]).llm_filled_at is not None
    assert _get(ids[1]).llm_filled_at is None and _get(ids[0]).llm_filled_at is None


async def test_attempts_capped_at_3(uid):
    jid = _job(uid)
    llm = FakeLLM(exc=LLMInvalidResponse("bad"))
    for _ in range(5):
        await _run(uid, llm)
    assert len(llm.calls) == 3
    j = _get(jid)
    assert j.fill_attempts == 3 and j.llm_filled_at is None


async def test_llm_error_counts_attempt(uid):
    jid = _job(uid)
    await _run(uid, FakeLLM(exc=LLMError("x")))
    assert _get(jid).fill_attempts == 1


async def test_rate_limited_pauses_without_counting(uid, caplog):
    a, b = _job(uid, 0), _job(uid, 1)
    now = [100.0]
    llm = FakeLLM(exc=LLMRateLimited("slow"))
    with caplog.at_level(logging.WARNING, logger="worker.collect_jobs"):
        assert await _run(uid, llm, clock=lambda: now[0]) == 0
        assert len(llm.calls) == 1  # batch stopped
        assert await _run(uid, llm, clock=lambda: now[0] + 30) == 0
        assert len(llm.calls) == 1  # still paused
    assert len([r for r in caplog.records if "rate limited" in r.message]) == 1
    assert _get(a).fill_attempts == 0 and _get(b).fill_attempts == 0
    llm.exc = None
    assert await _run(uid, llm, clock=lambda: now[0] + 61) == 2
    assert len(llm.calls) == 3


async def test_no_key_noop_warn_once(uid, caplog):
    jid = _job(uid)
    with caplog.at_level(logging.WARNING, logger="worker.collect_jobs"):
        assert await _run(uid, None) == 0
        assert await _run(uid, None) == 0
    assert len([r for r in caplog.records if "GROQ_API_KEY" in r.message]) == 1
    j = _get(jid)
    assert j.llm_filled_at is None and j.fill_attempts == 0


async def test_unexpected_error_isolated(uid, caplog):
    a, b = _job(uid, 0), _job(uid, 1)  # b is newest: processed first
    llm = FakeLLM(exc=[RuntimeError(SECRET), None])
    with caplog.at_level(logging.WARNING):
        assert await _run(uid, llm) == 2
    assert _get(b).fill_attempts == 1 and _get(b).llm_filled_at is None
    assert _get(a).llm_filled_at is not None
    assert SECRET not in caplog.text


async def test_user_scope(uid):
    with SessionLocal() as s:
        other = User(email=f"fill-jobs-other-{uuid.uuid4().hex}@example.test")
        s.add(other)
        s.commit()
        other_id = other.id
    try:
        mine, theirs = _job(uid), _job(other_id)
        await _run(uid, FakeLLM())
        assert _get(mine).llm_filled_at is not None
        assert _get(theirs).llm_filled_at is None
    finally:
        with SessionLocal() as s:
            s.execute(delete(Job).where(Job.user_id == other_id))
            s.execute(delete(Company).where(Company.user_id == other_id))
            s.execute(delete(User).where(User.id == other_id))
            s.commit()


async def test_no_posting_text_in_logs(uid, caplog):
    _job(uid, 0, description=SECRET, title=SECRET)
    _job(uid, 1, description=SECRET, title=SECRET)
    _job(uid, 2, description=SECRET, title=SECRET)
    llm = FakeLLM(exc=[
        LLMInvalidResponse(SECRET), LLMError(SECRET), RuntimeError(SECRET)])
    with caplog.at_level(logging.DEBUG):
        await _run(uid, llm)
        await _run(uid, FakeLLM(exc=LLMRateLimited(SECRET)))
    assert SECRET not in caplog.text


async def test_content_hash_changed_meanwhile_discards_result(uid):
    jid = _job(uid, description=PAY)

    def ingest_rewrites():
        with SessionLocal() as s:
            s.execute(update(Job).where(Job.id == jid).values(
                content_hash="new", llm_filled_at=None, fill_attempts=0))
            s.commit()

    llm = FakeLLM(FillResult(work_model="remote"), hook=ingest_rewrites)
    await _run(uid, llm)
    j = _get(jid)
    assert j.work_model == "unknown" and j.llm_filled_at is None


async def test_no_transaction_open_during_llm_call(uid):
    jid = _job(uid)
    locked = []

    def probe():
        with SessionLocal() as s:
            row = s.execute(
                select(Job.id).where(Job.id == jid).with_for_update(nowait=True)
            ).first()
            locked.append(row)

    await _run(uid, FakeLLM(hook=probe))
    assert locked == [(jid,)]


async def test_cancelled_propagates_without_partial_state(uid):
    jid = _job(uid)
    with pytest.raises(asyncio.CancelledError):
        await _run(uid, FakeLLM(exc=asyncio.CancelledError()))
    j = _get(jid)
    assert j.llm_filled_at is None and j.fill_attempts == 0


async def test_260_jobs_one_needing_one_call_per_tick(uid):
    needing = _job(uid, 0)  # oldest needs the LLM
    with SessionLocal() as s:
        c = s.execute(select(Company).where(Company.user_id == uid)).scalars().first()
        base = datetime(2026, 2, 1, tzinfo=UTC)
        s.add_all([
            Job(user_id=uid, company_id=c.id, external_id=f"x{i}", url="https://e.test",
                title="t", description="Plain.", raw={}, content_hash=f"x{i}",
                fingerprint=f"xf{i}", status="open", work_model="remote",
                first_seen_at=base + timedelta(minutes=i))
            for i in range(259)
        ])
        s.commit()
        s.execute(update(Job).where(Job.id == needing).values(
            first_seen_at=base + timedelta(days=1)))  # newest: in the first window
        s.commit()
    llm = FakeLLM(exc=LLMInvalidResponse("bad"))
    assert await _run(uid, llm) == 1
    assert len(llm.calls) == 1
    assert _get(needing).fill_attempts == 1


async def test_60_jobs_two_needing_two_calls_one_attempt_each(uid):
    # the 2 needing jobs are the newest, so they sit in the FIRST (full, 50-row)
    # window; 10 older rows force a second window that would re-pick them
    for i in range(60):
        _job(uid, i, work_model="unknown" if i >= 58 else "remote")
    needing = [_job_id(uid, 58), _job_id(uid, 59)]
    llm = FakeLLM(exc=LLMInvalidResponse("bad"))
    assert await _run(uid, llm) == 2
    assert len(llm.calls) == 2
    assert [_get(j).fill_attempts for j in needing] == [1, 1]


def _job_id(uid, i):
    with SessionLocal() as s:
        return s.scalar(select(Job.id).where(Job.user_id == uid, Job.external_id == f"e{i}"))


async def test_persisted_amounts_have_two_decimals(uid):
    jid = _job(uid, description="Salary: $120,000 - $150,000 per year")
    await _run(uid, FakeLLM(FillResult(
        salary_min=Decimal(120000), salary_max=Decimal(150000),
        salary_currency="USD", salary_period="year")))
    j = _get(jid)
    assert j.salary_known and j.salary_min == Decimal("120000.00")
