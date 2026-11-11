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
        await asyncio.sleep(0.05)
        if self.exc:
            raise self.exc
        return CanonicalProfile.model_validate({"summary": "Dev Python"})


async def test_import_success_sets_ready_and_extracted(uid):
    iid = _add_import(uid)
    assert await process_pending_imports(SessionLocal, FakeLLM(), 1000) == 1
    imp = _get(iid)
    assert imp.status == "ready"
    assert imp.extracted["summary"] == "Dev Python"
    assert imp.truncated is False


async def test_import_llm_error_sets_error_and_no_extracted(uid):
    iid = _add_import(uid)
    n = await process_pending_imports(
        SessionLocal, FakeLLM(LLMInvalidResponse("bad")), 1000
    )
    imp = _get(iid)
    assert n == 1 and imp.status == "error" and imp.extracted is None
    assert imp.error and "bad" not in imp.error


async def test_import_rate_limited_goes_back_to_pending(uid):
    iid = _add_import(uid)
    n = await process_pending_imports(
        SessionLocal, FakeLLM(LLMRateLimited("slow")), 1000
    )
    imp = _get(iid)
    assert n == 0 and imp.status == "pending" and imp.extracted is None


async def test_no_llm_leaves_pending_without_crashing(uid, caplog):
    iid = _add_import(uid)
    jobs._warned_no_llm = False
    with caplog.at_level("WARNING"):
        assert await process_pending_imports(SessionLocal, None, 1000) == 0
        assert await process_pending_imports(SessionLocal, None, 1000) == 0
    assert _get(iid).status == "pending"
    assert sum("GROQ_API_KEY" in r.message for r in caplog.records) == 1


async def test_two_workers_do_not_take_same_import(uid):
    iid = _add_import(uid)
    llm = FakeLLM()
    a, b = await asyncio.gather(
        process_pending_imports(SessionLocal, llm, 1000),
        process_pending_imports(SessionLocal, llm, 1000),
    )
    assert sorted([a, b]) == [0, 1]
    assert llm.calls == 1
    assert _get(iid).status == "ready"


def test_startup_resets_stuck_extracting(uid):
    stuck = _add_import(uid, status="extracting")
    done = _add_import(uid, status="ready")
    assert reset_stuck_imports(SessionLocal) >= 1
    assert _get(stuck).status == "pending"
    assert _get(done).status == "ready"


class FakeEmbedder:
    dimensions = 1024
    model_version = "fake@1"

    def __init__(self, fail_on_call=None):
        self.calls: list[list[str]] = []
        self.fail_on_call = fail_on_call

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail_on_call == len(self.calls):
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


async def test_embed_fills_pending_in_batches(uid):
    _add_chunks(uid, 5)
    emb = FakeEmbedder()
    assert await embed_pending_chunks(SessionLocal, emb, 2) == 5
    assert [len(c) for c in emb.calls] == [2, 2, 1]
    rows = _embedded(uid)
    assert len(rows) == 5 and {r.model_version for r in rows} == {"fake@1"}


async def test_embed_current_version_first(uid):
    _add_chunks(uid, 2, version=1)
    _add_chunks(uid, 2, version=2)
    emb = FakeEmbedder()
    await embed_pending_chunks(SessionLocal, emb, 2)
    assert all(t.startswith("t2-") for t in emb.calls[0])


async def test_embed_skips_when_nothing_pending_without_loading_model(
    uid, monkeypatch
):
    embedder = BgeM3Embedder()

    def boom(*a, **k):
        raise AssertionError("model must not be loaded")

    monkeypatch.setattr(embedder, "_load", boom)
    assert await embed_pending_chunks(SessionLocal, embedder, 4) == 0


async def test_embed_failure_midway_keeps_committed_batches(uid):
    _add_chunks(uid, 5)
    emb = FakeEmbedder(fail_on_call=2)
    with pytest.raises(RuntimeError):
        await embed_pending_chunks(SessionLocal, emb, 2)
    assert len(_embedded(uid)) == 2


def test_model_version_includes_max_tokens():
    assert BgeM3Embedder(max_tokens=256).model_version == "bge-m3@1:max256"
