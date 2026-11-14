import uuid

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.matching.quality import (
    build_sample,
    evaluate,
    get_seed,
    new_seed,
    precision_at_k,
)
from rumo_core.models import (
    Company,
    Job,
    JobLabel,
    Match,
    Profile,
    ProfileChunk,
    User,
    UserPreference,
)

MV = "bge-m3@test"
DIM = 1024
db = pytest.mark.db


def vec(t: float) -> list[float]:
    """Unit vector whose cosine to e0 is t (higher t = better job)."""
    v = [0.0] * DIM
    v[0] = t
    v[1] = (1 - t * t) ** 0.5
    return v


@pytest.fixture
def env():
    created: list[int] = []
    with SessionLocal() as s:
        counter = iter(range(100000))

        def new_user():
            u = User(email=f"quality-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
            s.add(c)
            s.commit()
            s.add(Profile(user_id=u.id, version=1, data={}, content_hash="h"))
            s.add(ProfileChunk(
                user_id=u.id, profile_version=1, source_ref="r0", kind="note",
                text="t", content_hash="h", embedding=vec(1.0), model_version=MV,
            ))
            s.commit()
            return u.id, c.id

        def add_job(uid, cid, t=None, **kw):
            n = next(counter)
            j = Job(
                user_id=uid, company_id=cid, external_id=str(n),
                url="https://example.test/j", title=f"t{n}", description="d",
                raw={}, content_hash="h", fingerprint=f"f{n}",
                embedding=vec(t) if t is not None else None,
                model_version=MV if t is not None else None, **kw,
            )
            s.add(j)
            s.commit()
            return j.id

        def label(uid, job_id, lab):
            s.add(JobLabel(user_id=uid, job_id=job_id, label=lab))
            s.commit()

        yield s, new_user, add_job, label
        s.rollback()
        for uid in created:
            s.execute(delete(JobLabel).where(JobLabel.user_id == uid))
            s.execute(delete(Match).where(Match.user_id == uid))
            s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
            s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


def ranked_jobs(env, uid, cid, n):
    """n jobs with strictly decreasing score; returns ids best first."""
    _, _, add_job, _ = env
    return [add_job(uid, cid, t=0.99 - i * 0.005) for i in range(n)]


def test_precision_at_k_manual_cases():
    assert precision_at_k({1: "good", 2: "bad", 3: "good"}, [3, 2, 1], k=2) == 0.5
    assert precision_at_k({1: "good"}, [1], k=10) == 1.0
    assert precision_at_k({}, [], k=10) == 0.0
    assert precision_at_k({1: "bad"}, [9, 1], k=2) == 0.0


@db
def test_sample_stable_with_same_seed(env):
    s, new_user, _, label = env
    uid, cid = new_user()
    ids = ranked_jobs(env, uid, cid, 120)
    label(uid, ids[0], "good")
    a = build_sample(s, uid, model_version=MV)
    b = build_sample(s, uid, model_version=MV)
    assert a == b
    assert len(a) == 40
    top = set(ids[:50])
    assert sum(1 for j in a if j in top) == 20
    assert sum(1 for j in a if j not in top) == 20
    seed = get_seed(s, uid)
    assert new_seed(s, uid) != seed
    c = build_sample(s, uid, model_version=MV)
    assert c != a
    assert s.scalar(select(JobLabel.id).where(JobLabel.user_id == uid)) is not None


@db
def test_seed_upsert_keeps_other_columns(env):
    s, new_user, _, _ = env
    uid, _ = new_user()
    s.add(UserPreference(user_id=uid, discovery_enabled=False,
                         target_companies=["x"]))
    s.commit()
    seed = get_seed(s, uid)
    assert 0 <= seed < 2**31 - 1
    assert get_seed(s, uid) == seed
    n = new_seed(s, uid)
    s.expire_all()
    pref = s.get(UserPreference, uid)
    assert pref.quality_seed == n
    assert pref.discovery_enabled is False
    assert pref.target_companies == ["x"]


@db
def test_sample_small_dataset(env):
    s, new_user, _, _ = env
    uid, cid = new_user()
    ranked_jobs(env, uid, cid, 30)
    a = build_sample(s, uid, model_version=MV)
    assert len(a) == 20


@db
def test_sample_without_profile_vector_is_empty(env):
    s, new_user, add_job, _ = env
    uid, cid = new_user()
    s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
    s.commit()
    add_job(uid, cid, t=0.9)
    assert build_sample(s, uid, model_version=MV) == []


@db
def test_report_insufficient_lists_whats_missing(env):
    s, new_user, _, label = env
    uid, cid = new_user()
    ids = ranked_jobs(env, uid, cid, 18)
    for i, j in enumerate(ids):
        label(uid, j, "good" if i < 10 else "bad")
    r = evaluate(s, uid, model_version=MV)
    assert r.status == "insuficiente"
    assert "faltam 12 rótulos" in r.missing
    assert "faltam 2 vagas ruins" in r.missing
    assert r.precision_at_10 is None
    assert r.precision_vec_at_10 is None


def _known_case(env):
    s, new_user, _, label = env
    uid, cid = new_user()
    ids = ranked_jobs(env, uid, cid, 32)
    good = set(range(3, 10)) | set(range(27, 32))  # v4-v10, v28-v32
    for i, j in enumerate(ids):
        label(uid, j, "good" if i in good else "bad")
    return s, uid, ids


@db
def test_report_known_case(env):
    s, uid, ids = _known_case(env)
    s.add(Match(user_id=uid, job_id=ids[19], profile_version=1, vec_score=0.0,
                ai_score=99, content_hash="h", state="ok"))
    s.commit()
    r = evaluate(s, uid, model_version=MV)
    assert (r.good, r.bad, r.unranked) == (12, 20, 0)
    assert r.precision_vec_at_10 == 0.7
    assert r.precision_at_10 == 0.6
    assert r.status == "dentro_da_meta"
    got = {(m.job_id, m.label, m.position) for m in r.misranked}
    assert (ids[19], "bad", 1) in got
    assert (ids[9], "good", 11) in got


@db
def test_report_ties_by_job_id(env):
    s, uid, ids = _known_case(env)
    _, _, add_job, label = env
    a = add_job(uid, s.scalar(select(Job.company_id).where(Job.id == ids[0])), t=0.999)
    b = add_job(uid, s.scalar(select(Job.company_id).where(Job.id == ids[0])), t=0.999)
    label(uid, b, "good")
    label(uid, a, "bad")
    r = evaluate(s, uid, model_version=MV)
    pos = {m.job_id: m.position for m in r.misranked}
    # a (lower id) comes first: bad in top 10 at position 1; b second, good
    assert pos[a] == 1
    assert b not in pos


@db
def test_report_below_target(env):
    s, new_user, _, label = env
    uid, cid = new_user()
    ids = ranked_jobs(env, uid, cid, 32)
    for i, j in enumerate(ids):
        label(uid, j, "bad" if i < 10 or i >= 22 else "good")
    r = evaluate(s, uid, model_version=MV)
    assert r.precision_at_10 == 0.0
    assert r.status == "abaixo_da_meta"


@db
def test_unranked_labels_do_not_count(env):
    s, new_user, add_job, label = env
    uid, cid = new_user()
    ids = ranked_jobs(env, uid, cid, 5)
    nov = add_job(uid, cid, t=None)
    for j in ids:
        label(uid, j, "good")
    label(uid, nov, "bad")
    r = evaluate(s, uid, model_version=MV)
    assert r.labeled == 5
    assert r.unranked == 1
    assert r.bad == 0


@db
def test_report_scoped_by_user(env):
    s, new_user, _, label = env
    uid, cid = new_user()
    other, ocid = new_user()
    ids = ranked_jobs(env, other, ocid, 40)
    for i, j in enumerate(ids):
        label(other, j, "good" if i % 2 else "bad")
    r = evaluate(s, uid, model_version=MV)
    assert r.labeled == 0
    assert r.status == "insuficiente"
