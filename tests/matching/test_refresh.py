import uuid

import pytest
from sqlalchemy import delete, select, update

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.refresh import RefreshResult, mark_pending
from rumo_core.models import Company, Job, Match, Profile, ProfileChunk, User

pytestmark = pytest.mark.db

MV = "bge-m3@test"
DIM = 1024
F = SearchFilters()


def e(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


@pytest.fixture
def env():
    created: list[int] = []
    counter = iter(range(100000))

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"refresh-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
            s.add(c)
            s.commit()
            return u.id, c.id

    def add_job(uid, cid, emb, **kw):
        n = next(counter)
        with SessionLocal() as s:
            j = Job(
                user_id=uid, company_id=cid, external_id=str(n),
                url="https://example.test/j", title=f"t{n}", description="d",
                raw={}, content_hash=f"h{n}", fingerprint=f"f{n}",
                embedding=emb, model_version=MV, **kw,
            )
            s.add(j)
            s.commit()
            return j.id

    def add_profile(uid, version, emb):
        with SessionLocal() as s:
            s.add(Profile(user_id=uid, version=version, data={}, content_hash="h"))
            s.add(ProfileChunk(
                user_id=uid, profile_version=version, source_ref="r0", kind="note",
                text="t", content_hash="h", embedding=emb, model_version=MV,
            ))
            s.commit()

    yield new_user, add_job, add_profile
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(Match).where(Match.user_id == uid))
            s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


def _mark(uid, n=10):
    with SessionLocal() as s:
        return mark_pending(s, uid, F, model_version=MV, n=n)


def _matches(uid):
    with SessionLocal() as s:
        rows = s.execute(
            select(Match).where(Match.user_id == uid).order_by(Match.job_id)
        ).scalars().all()
        s.expunge_all()
        return rows


def test_mark_pending_top_n_only(env):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, 1, e(0))
    best = add_job(uid, cid, e(0))
    second = add_job(uid, cid, [0.6, 0.8] + [0.0] * (DIM - 2))
    add_job(uid, cid, e(1))
    res = _mark(uid, n=2)
    assert res == RefreshResult(requested=2, created=2, reset=0, already_ok=0, profile_version=1)
    rows = _matches(uid)
    assert sorted(m.job_id for m in rows) == sorted([best, second])
    by_job = {m.job_id: m for m in rows}
    assert all(m.state == "pendente" and m.profile_version == 1 for m in rows)
    assert by_job[best].vec_score == pytest.approx(1.0, abs=1e-6)
    assert by_job[second].vec_score == pytest.approx(1 - (1 - 0.6) / 2, abs=1e-6)
    with SessionLocal() as s:
        assert by_job[best].content_hash == s.get(Job, best).content_hash


def test_mark_pending_idempotent(env):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, 1, e(0))
    add_job(uid, cid, e(0))
    add_job(uid, cid, e(1))
    first = _mark(uid)
    assert first.created == 2
    again = _mark(uid)
    assert again == RefreshResult(requested=2, created=0, reset=0, already_ok=0, profile_version=1)
    assert len(_matches(uid)) == 2


def _set_ok(uid, job_id, content_hash):
    with SessionLocal() as s:
        s.execute(update(Match).where(Match.user_id == uid, Match.job_id == job_id).values(
            state="ok", ai_score=80, rationale="r", strengths=["s"], gaps=[],
            content_hash=content_hash, attempts=1,
        ))
        s.commit()


def test_ok_row_kept_when_job_unchanged(env):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, 1, e(0))
    jid = add_job(uid, cid, e(0))
    _mark(uid)
    with SessionLocal() as s:
        h = s.get(Job, jid).content_hash
    _set_ok(uid, jid, h)
    res = _mark(uid)
    assert (res.created, res.reset, res.already_ok) == (0, 0, 1)
    [m] = _matches(uid)
    assert m.state == "ok" and m.ai_score == 80 and m.attempts == 1


def test_ok_row_reset_when_job_content_changed(env):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, 1, e(0))
    changed = add_job(uid, cid, e(0))
    errored = add_job(uid, cid, e(1))
    _mark(uid)
    _set_ok(uid, changed, "old-hash")
    with SessionLocal() as s:
        s.execute(update(Match).where(Match.job_id == errored).values(
            state="erro", error="MatchInvalid", attempts=3))
        s.commit()
    res = _mark(uid)
    assert (res.created, res.reset, res.already_ok) == (0, 2, 0)
    for m in _matches(uid):
        assert m.state == "pendente" and m.attempts == 0 and m.error is None
        assert m.ai_score is None
    assert _mark(uid).reset == 0  # idempotent


def test_no_profile_creates_nothing(env):
    new_user, add_job, _ = env
    uid, cid = new_user()
    add_job(uid, cid, e(0))
    res = _mark(uid)
    assert res == RefreshResult(requested=0, created=0, reset=0, already_ok=0, profile_version=None)
    assert _matches(uid) == []


def test_refresh_scoped_by_user(env):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    other, ocid = new_user()
    add_profile(uid, 1, e(0))
    add_profile(other, 1, e(0))
    mine = add_job(uid, cid, e(0))
    theirs = add_job(other, ocid, e(0))
    _mark(other)
    _set_ok(other, theirs, "old-hash")
    res = _mark(uid)
    assert res.created == 1
    assert [m.job_id for m in _matches(uid)] == [mine]
    [t] = _matches(other)
    assert t.state == "ok" and t.content_hash == "old-hash"


def test_only_current_profile_version(env):
    new_user, add_job, add_profile = env
    uid, cid = new_user()
    add_profile(uid, 1, e(0))
    jid = add_job(uid, cid, e(0))
    _mark(uid)
    add_profile(uid, 2, e(0))
    res = _mark(uid)
    assert res.profile_version == 2 and res.created == 1
    versions = sorted(m.profile_version for m in _matches(uid) if m.job_id == jid)
    assert versions == [1, 2]
