import uuid

import pytest
from sqlalchemy import delete, select, update

from rumo_core.db import SessionLocal
from rumo_core.models import SearchProfile, User
from worker import embed_queries as eq
from worker.embed_queries import embed_pending_queries

pytestmark = pytest.mark.db

SECRET = "SECRET-QUERY-TEXT"


class FakeEmbedder:
    dimensions = 1024

    def __init__(self, fail_on_calls=(), version="fake@1", on_embed=None):
        self.model_version = version
        self.calls: list[list[str]] = []
        self.fail_on_calls = set(fail_on_calls)
        self.on_embed = on_embed

    def embed(self, texts):
        self.calls.append(list(texts))
        if len(self.calls) in self.fail_on_calls:
            raise RuntimeError(f"boom {SECRET}")
        if self.on_embed:
            self.on_embed()
        return [[0.5] * 1024 for _ in texts]


@pytest.fixture(autouse=True)
def _state():
    eq._reset_state()
    yield
    eq._reset_state()


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"embed-queries-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(SearchProfile).where(SearchProfile.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def _profile(uid, text, name="default"):
    with SessionLocal() as s:
        p = SearchProfile(user_id=uid, name=name, filters={}, query_text=text)
        s.add(p)
        s.commit()
        return p.id


def _get(pid):
    with SessionLocal() as s:
        return s.get(SearchProfile, pid)


async def _run(uid, emb, **kw):
    return await embed_pending_queries(SessionLocal, emb, user_id=uid, **kw)


async def test_embeds_pending_queries(uid):
    pid = _profile(uid, "dados")
    assert await _run(uid, FakeEmbedder()) == 1
    p = _get(pid)
    assert len(p.query_embedding) == 1024
    assert p.query_model_version == "fake@1"


async def test_skips_already_embedded(uid):
    pid = _profile(uid, "dados")
    other = _profile(uid, None, name="vazio")
    emb = FakeEmbedder()
    assert await _run(uid, emb) == 1
    assert await _run(uid, emb) == 0
    assert len(emb.calls) == 1
    assert _get(other).query_embedding is None
    assert _get(pid).query_embedding is not None


async def test_model_version_change_reembeds(uid):
    pid = _profile(uid, "dados")
    await _run(uid, FakeEmbedder())
    assert await _run(uid, FakeEmbedder(version="fake@2")) == 1
    assert _get(pid).query_model_version == "fake@2"


async def test_embedder_failure_does_not_raise_and_backs_off(uid, caplog):
    pid = _profile(uid, SECRET)
    emb = FakeEmbedder(fail_on_calls={1})
    now = [100.0]
    clock = lambda: now[0]  # noqa: E731
    with caplog.at_level("DEBUG"):
        assert await _run(uid, emb, clock=clock) == 0
    assert _get(pid).query_embedding is None
    assert SECRET not in caplog.text
    assert "RuntimeError" in caplog.text
    now[0] += 4
    assert await _run(uid, emb, clock=clock) == 0
    assert len(emb.calls) == 1
    now[0] += 2
    assert await _run(uid, emb, clock=clock) == 1
    assert _get(pid).query_embedding is not None


async def test_text_changed_during_embedding_is_not_written(uid):
    pid = _profile(uid, "dados")

    def change():
        with SessionLocal() as s:
            s.execute(
                update(SearchProfile).where(SearchProfile.id == pid)
                .values(query_text="produto")
            )
            s.commit()

    assert await _run(uid, FakeEmbedder(on_embed=change)) == 0
    p = _get(pid)
    assert p.query_text == "produto" and p.query_embedding is None
    assert await _run(uid, FakeEmbedder()) == 1


def test_registered_after_embed_chunks_every_15s():
    from rumo_core.config import Settings
    from worker.main import build_registry

    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/x")
    reg = build_registry(s, object(), None, object())
    names = list(reg._jobs)
    assert names[names.index("embed_chunks") + 1] == "embed_queries"
    assert reg._jobs["embed_queries"][1] == 15
