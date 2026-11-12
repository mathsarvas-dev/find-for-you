import asyncio

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import BgeM3Embedder
from rumo_core.llm.base import LLMInvalidResponse, LLMRateLimited
from rumo_core.models import ProfileChunk, ProfileImport, User
from rumo_core.profile.schema import CanonicalProfile
from worker import jobs
from worker.jobs import (
    embed_pending_chunks,
    process_pending_imports,
    reset_stuck_imports,
)

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _fresh_state():
    jobs._reset_state()
    yield
    jobs._reset_state()


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email="worker-jobs-test@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == user_id))
        s.execute(delete(ProfileImport).where(ProfileImport.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def _add_import(uid, status="pending", text="Joao, dev Python"):
    with SessionLocal() as s:
        imp = ProfileImport(
            user_id=uid, filename="cv.txt", content_type="text/plain",
            raw_text=text, status=status,
        )
        s.add(imp)
        s.commit()
        return imp.id


def _get(import_id):
    with SessionLocal() as s:
        return s.get(ProfileImport, import_id)


class FakeLLM:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = 0

    async def complete_json(self, messages, schema):
        self.calls += 1
        if self.exc:
            raise self.exc
        return CanonicalProfile.model_validate({"summary": "Dev Python"})


async def _run(uid, llm, **kw):
    return await process_pending_imports(SessionLocal, llm, 1000, user_id=uid, **kw)


async def test_import_success_sets_ready_and_extracted(uid):
    iid = _add_import(uid)
    assert await _run(uid, FakeLLM()) == 1
    imp = _get(iid)
    assert imp.status == "ready"
    assert imp.extracted["summary"] == "Dev Python"
    assert imp.truncated is False


async def test_import_llm_error_sets_error_and_no_extracted(uid):
    iid = _add_import(uid)
    n = await _run(uid, FakeLLM(LLMInvalidResponse("bad")))
    imp = _get(iid)
    assert n == 1 and imp.status == "error" and imp.extracted is None
    assert imp.error and "bad" not in imp.error


async def test_unexpected_error_marks_error_and_does_not_block_next(uid, caplog):
    first = _add_import(uid)
    second = _add_import(uid)
    with caplog.at_level("ERROR"):
        n = await _run(uid, FakeLLM(RuntimeError("kaboom")))
    assert n == 1
    imp = _get(first)
    assert imp.status == "error" and imp.error == "Falha inesperada ao ler o currículo."
    assert imp.extracted is None
    assert _get(second).status == "pending"
    assert sum("unexpected failure" in r.message for r in caplog.records) == 1
    # the next import is picked up on the following tick
    assert await _run(uid, FakeLLM()) == 1
    assert _get(second).status == "ready"


async def test_rate_limited_goes_back_to_pending_and_pauses(uid, caplog):
    iid = _add_import(uid)
    llm = FakeLLM(LLMRateLimited("slow"))
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    with caplog.at_level("WARNING"):
        assert await _run(uid, llm, clock=clock) == 0
        assert _get(iid).status == "pending" and llm.calls == 1
        # paused: no claim, no LLM call, no extra warning
        now[0] += 30
        assert await _run(uid, llm, clock=clock) == 0
        assert llm.calls == 1
        assert sum("rate limited" in r.message for r in caplog.records) == 1
    # pause over: works again
    now[0] += 31
    ok = FakeLLM()
    assert await _run(uid, ok, clock=clock) == 1
    assert _get(iid).status == "ready"


async def test_no_llm_leaves_pending_without_crashing(uid, caplog):
    iid = _add_import(uid)
    with caplog.at_level("WARNING"):
        assert await _run(uid, None) == 0
        assert await _run(uid, None) == 0
    assert _get(iid).status == "pending"
    assert sum("GROQ_API_KEY" in r.message for r in caplog.records) == 1


def test_claim_skips_row_locked_by_another_session(uid):
    locked = _add_import(uid)
    other = _add_import(uid)
    with SessionLocal() as holder:
        holder.execute(
            select(ProfileImport).where(ProfileImport.id == locked).with_for_update()
        ).scalar_one()
        claimed = jobs._claim_import(SessionLocal, uid)
        assert claimed is not None and claimed[0] == other
        assert jobs._claim_import(SessionLocal, uid) is None  # only locked one left
        holder.rollback()
    assert _get(locked).status == "pending"
    assert _get(other).status == "extracting"


def test_startup_resets_stuck_extracting(uid):
    stuck = _add_import(uid, status="extracting")
    done = _add_import(uid, status="ready")
    assert reset_stuck_imports(SessionLocal, user_id=uid) == 1
    assert _get(stuck).status == "pending"
    assert _get(done).status == "ready"


class FakeEmbedder:
    dimensions = 1024

    def __init__(self, fail_on_calls=(), version="fake@1"):
        self.model_version = version
        self.calls: list[list[str]] = []
        self.fail_on_calls = set(fail_on_calls)

    def embed(self, texts):
        self.calls.append(list(texts))
        if len(self.calls) in self.fail_on_calls:
            raise RuntimeError("boom")
        return [[0.5] * 1024 for _ in texts]


def _add_chunks(uid, n, version=1, start=0):
    with SessionLocal() as s:
        s.add_all(
            ProfileChunk(
                user_id=uid, profile_version=version, source_ref=f"r{start + i}",
                kind="experience", text=f"t{version}-{start + i}",
                content_hash=f"h{version}-{start + i}",
            )
            for i in range(n)
        )
        s.commit()


def _embedded(uid):
    with SessionLocal() as s:
        return s.execute(
            select(ProfileChunk).where(
                ProfileChunk.user_id == uid, ProfileChunk.embedding.is_not(None)
            )
        ).scalars().all()


async def _embed(uid, emb, batch=2, **kw):
    return await embed_pending_chunks(SessionLocal, emb, batch, user_id=uid, **kw)


async def test_embed_fills_pending_in_batches(uid):
    _add_chunks(uid, 5)
    emb = FakeEmbedder()
    assert await _embed(uid, emb) == 5
    assert [len(c) for c in emb.calls] == [2, 2, 1]
    rows = _embedded(uid)
    assert len(rows) == 5 and {r.model_version for r in rows} == {"fake@1"}


async def test_embed_current_version_first(uid):
    _add_chunks(uid, 2, version=1)
    _add_chunks(uid, 2, version=2)
    emb = FakeEmbedder()
    await _embed(uid, emb)
    assert all(t.startswith("t2-") for t in emb.calls[0])


async def test_embed_reembeds_chunks_with_old_model_version(uid):
    _add_chunks(uid, 2)
    old = FakeEmbedder(version="old@1")
    await _embed(uid, old)
    assert {r.model_version for r in _embedded(uid)} == {"old@1"}
    new = FakeEmbedder(version="new@2")
    assert await _embed(uid, new) == 2
    assert {r.model_version for r in _embedded(uid)} == {"new@2"}
    # nothing left to do now
    assert await _embed(uid, new) == 0


async def test_embed_skips_when_nothing_pending_without_loading_model(
    uid, monkeypatch
):
    embedder = BgeM3Embedder()

    def boom(*a, **k):
        raise AssertionError("model must not be loaded")

    monkeypatch.setattr(embedder, "_load", boom)
    assert await _embed(uid, embedder, batch=4) == 0


async def test_embed_skips_chunks_locked_by_another_session(uid):
    _add_chunks(uid, 2)
    with SessionLocal() as s:
        first = s.execute(
            select(ProfileChunk).where(ProfileChunk.user_id == uid).order_by(ProfileChunk.id)
        ).scalars().first()
        first_id = first.id
    with SessionLocal() as holder:
        holder.execute(
            select(ProfileChunk).where(ProfileChunk.id == first_id).with_for_update()
        ).scalar_one()
        assert await _embed(uid, FakeEmbedder(), batch=10) == 1
        holder.rollback()
    assert [r.id for r in _embedded(uid)] != [first_id]


async def test_embed_failure_midway_keeps_committed_batches_and_backs_off(
    uid, caplog
):
    _add_chunks(uid, 5)
    emb = FakeEmbedder(fail_on_calls={2})
    now = [100.0]
    clock = lambda: now[0]  # noqa: E731
    with caplog.at_level("ERROR"):
        assert await _embed(uid, emb, clock=clock) == 2  # no raise
    assert len(_embedded(uid)) == 2
    assert sum("embedding failed" in r.message for r in caplog.records) == 1
    # in backoff window (5s): nothing happens, embedder not called
    calls = len(emb.calls)
    now[0] += 4
    assert await _embed(uid, emb, clock=clock) == 0
    assert len(emb.calls) == calls
    # backoff over: resumes and finishes (call 3+ succeed)
    now[0] += 2
    assert await _embed(uid, emb, clock=clock) == 3
    assert len(_embedded(uid)) == 5


async def test_embed_backoff_grows_exponentially_and_is_capped(uid):
    _add_chunks(uid, 1)
    emb = FakeEmbedder(fail_on_calls=set(range(1, 100)))
    now = [0.0]
    clock = lambda: now[0]  # noqa: E731
    delays = []
    for _ in range(9):
        await _embed(uid, emb, clock=clock)
        delays.append(jobs._embed_next_try - now[0])
        now[0] = jobs._embed_next_try
    assert delays[:4] == [5, 10, 20, 40]
    assert max(delays) == jobs.EMBED_BACKOFF_CAP == delays[-1]


def test_model_version_includes_max_tokens():
    assert BgeM3Embedder(max_tokens=256).model_version == "bge-m3@1:max256"
