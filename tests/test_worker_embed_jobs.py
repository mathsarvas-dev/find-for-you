import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import BgeM3Embedder
from rumo_core.models import Company, Job, User
from worker import collect_jobs as cj
from worker import jobs
from worker.collect_jobs import embed_pending_jobs, pending_jobs_stmt

pytestmark = pytest.mark.db


class FakeEmbedder:
    dimensions = 1024

    def __init__(self, fail_on_calls=(), version="fake@1"):
        self.model_version = version
        self.calls: list[list[str]] = []
        self.fail_on_calls = set(fail_on_calls)

    def embed(self, texts):
        self.calls.append(list(texts))
        if len(self.calls) in self.fail_on_calls:
            raise RuntimeError("boom SECRET-JOB-TEXT")
        return [[0.5] * 1024 for _ in texts]


@pytest.fixture(autouse=True)
def _state():
    cj._reset_embed_state()
    yield
    cj._reset_embed_state()


def _mkuser(tag):
    with SessionLocal() as s:
        u = User(email=f"embed-jobs-{tag}-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        return u.id


def _cleanup(user_id):
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


@pytest.fixture
def uid():
    user_id = _mkuser("a")
    yield user_id
    _cleanup(user_id)


def _add_jobs(uid, n, status="open", start=0, description="Python backend."):
    with SessionLocal() as s:
        c = s.execute(select(Company).where(Company.user_id == uid)).scalars().first()
        if c is None:
            c = Company(user_id=uid, source="fake", identifier=f"co{uid}", name="Acme")
            s.add(c)
            s.flush()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(start, start + n):
            s.add(Job(
                user_id=uid, company_id=c.id, external_id=f"e{i}",
                url=f"https://example.test/{i}", title=f"Job {i}",
                description=description, raw={}, content_hash=f"h{i}",
                fingerprint=f"f{i}", status=status,
                first_seen_at=base + timedelta(minutes=i),
            ))
        s.commit()


def _embedded(uid):
    with SessionLocal() as s:
        return s.execute(
            select(Job).where(Job.user_id == uid, Job.embedding.is_not(None))
        ).scalars().all()


async def _embed(uid, emb, batch=2, **kw):
    return await embed_pending_jobs(SessionLocal, emb, batch, user_id=uid, **kw)


async def test_embeds_open_jobs_only(uid):
    _add_jobs(uid, 2, "open")
    _add_jobs(uid, 2, "closed", start=10)
    _add_jobs(uid, 2, "duplicate", start=20)
    emb = FakeEmbedder()
    assert await _embed(uid, emb, batch=10) == 2
    assert {j.status for j in _embedded(uid)} == {"open"}
    assert await _embed(uid, emb, batch=10) == 0


async def test_text_includes_company_name(uid):
    _add_jobs(uid, 1)
    emb = FakeEmbedder()
    await _embed(uid, emb)
    assert "Company: Acme" in emb.calls[0][0]


async def test_unchanged_not_reembedded_and_reopened_job_keeps_vector(uid):
    _add_jobs(uid, 2)
    emb = FakeEmbedder()
    assert await _embed(uid, emb) == 2
    with SessionLocal() as s:
        s.execute(update(Job).where(Job.user_id == uid).values(status="closed"))
        s.commit()
    assert await _embed(uid, emb) == 0
    with SessionLocal() as s:
        s.execute(update(Job).where(Job.user_id == uid).values(status="open"))
        s.commit()
        assert s.execute(pending_jobs_stmt("fake@1", uid)).all() == []
    assert await _embed(uid, emb) == 0
    assert len(emb.calls) == 1


async def test_cleared_embedding_is_reembedded(uid):
    _add_jobs(uid, 2)
    emb = FakeEmbedder()
    await _embed(uid, emb)
    with SessionLocal() as s:  # ingest clears the vector when content changes
        s.execute(
            update(Job).where(Job.user_id == uid, Job.external_id == "e0")
            .values(embedding=None)
        )
        s.commit()
    assert await _embed(uid, emb) == 1
    assert len(emb.calls) == 2


async def test_old_model_version_reembedded(uid):
    _add_jobs(uid, 2)
    await _embed(uid, FakeEmbedder(version="old@1"))
    assert {j.model_version for j in _embedded(uid)} == {"old@1"}
    assert await _embed(uid, FakeEmbedder(version="new@2")) == 2
    assert {j.model_version for j in _embedded(uid)} == {"new@2"}


async def test_batching_max_batches_newest_first_commit_per_batch(uid):
    _add_jobs(uid, 5)
    emb = FakeEmbedder()
    assert await _embed(uid, emb, batch=2) == 2  # default max_batches=1
    assert [len(c) for c in emb.calls] == [2]
    assert "Job 4" in emb.calls[0][0] and "Job 3" in emb.calls[0][1]
    assert await _embed(uid, emb, batch=2, max_batches=5) == 3
    assert [len(c) for c in emb.calls] == [2, 2, 1]
    assert len(_embedded(uid)) == 5


async def test_failure_commits_earlier_batches_backoff_no_text_in_logs(uid, caplog):
    _add_jobs(uid, 5, description="SECRET-JOB-TEXT")
    emb = FakeEmbedder(fail_on_calls={2})
    now = [100.0]
    clock = lambda: now[0]  # noqa: E731
    with caplog.at_level("INFO"):
        assert await _embed(uid, emb, clock=clock, max_batches=5) == 2  # no raise
    assert len(_embedded(uid)) == 2
    assert sum("job embedding failed" in r.message for r in caplog.records) == 1
    assert "SECRET-JOB-TEXT" not in caplog.text and "Job " not in caplog.text
    assert "RuntimeError" in caplog.text
    calls = len(emb.calls)
    now[0] += 4
    assert await _embed(uid, emb, clock=clock, max_batches=5) == 0
    assert len(emb.calls) == calls
    now[0] += 2
    assert await _embed(uid, emb, clock=clock, max_batches=5) == 3
    assert len(_embedded(uid)) == 5


async def test_backoff_grows_and_is_capped(uid):
    _add_jobs(uid, 1)
    emb = FakeEmbedder(fail_on_calls=set(range(1, 100)))
    now = [0.0]
    clock = lambda: now[0]  # noqa: E731
    delays = []
    for _ in range(9):
        await _embed(uid, emb, clock=clock)
        delays.append(cj._jobs_embed_next_try - now[0])
        now[0] = cj._jobs_embed_next_try
    assert delays[:4] == [5, 10, 20, 40]
    assert max(delays) == jobs.EMBED_BACKOFF_CAP == delays[-1]


async def test_backoff_state_separate_from_chunks(uid):
    _add_jobs(uid, 1)
    await _embed(uid, FakeEmbedder(fail_on_calls={1}), clock=lambda: 0.0)
    assert cj._jobs_embed_failures == 1
    assert jobs._embed_failures == 0 and jobs._embed_next_try == 0.0


async def test_model_not_loaded_when_nothing_pending(uid, monkeypatch):
    embedder = BgeM3Embedder()

    def boom(*a, **k):
        raise AssertionError("model must not be loaded")

    monkeypatch.setattr(embedder, "_load", boom)
    _add_jobs(uid, 2, "closed")
    assert await _embed(uid, embedder, batch=4) == 0


async def test_lock_skip(uid):
    _add_jobs(uid, 2)
    with SessionLocal() as s:
        first = s.execute(
            select(Job).where(Job.user_id == uid).order_by(Job.first_seen_at.desc(), Job.id)
        ).scalars().first()
        first_id = first.id
    with SessionLocal() as holder:
        holder.execute(select(Job).where(Job.id == first_id).with_for_update()).scalar_one()
        assert await _embed(uid, FakeEmbedder(), batch=10) == 1
        holder.rollback()
    assert [j.id for j in _embedded(uid)] != [first_id]


async def test_user_scope(uid):
    other = _mkuser("b")
    try:
        _add_jobs(uid, 1)
        _add_jobs(other, 1)
        assert await _embed(uid, FakeEmbedder()) == 1
        assert len(_embedded(other)) == 0
    finally:
        _cleanup(other)


async def test_shares_lock_with_chunk_embedding(uid):
    _add_jobs(uid, 1)
    assert jobs.EMBED_LOCK is jobs._embed_lock
    await jobs.EMBED_LOCK.acquire()
    try:
        task = asyncio.create_task(_embed(uid, FakeEmbedder()))
        await asyncio.sleep(0.2)
        assert not task.done()
    finally:
        jobs.EMBED_LOCK.release()
    assert await task == 1
