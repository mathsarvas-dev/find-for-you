import math
import uuid

import pytest
from sqlalchemy import delete

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.ranking import rank_jobs, top_candidate_ids
from rumo_core.models import Company, Job, Match, Profile, ProfileChunk, User

MV = "bge-m3@test"
DIM = 1024


def e(i: int, sign: float = 1.0) -> list[float]:
    """Unit basis vector e_i (orthogonal to every other e_j)."""
    v = [0.0] * DIM
    v[i] = sign
    return v


def mix(*idx: int) -> list[float]:
    """Normalized sum of basis vectors."""
    v = [0.0] * DIM
    n = len(idx) ** 0.5
    for i in idx:
        v[i] = 1.0 / n
    return v


@pytest.fixture
def env():
    created: list[int] = []
    with SessionLocal() as s:
        counter = iter(range(100000))

        def new_user():
            u = User(email=f"ranking-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
            s.add(c)
            s.commit()
            return u.id, c.id

        def add_job(uid, cid, embedding=None, model_version=MV, **kw):
            n = next(counter)
            j = Job(
                user_id=uid, company_id=cid, external_id=str(n),
                url="https://example.test/j", title=f"t{n}", description="d",
                raw={}, content_hash="h", fingerprint=f"f{n}",
                embedding=embedding,
                model_version=model_version if embedding is not None else None,
                **kw,
            )
            s.add(j)
            s.commit()
            return j.id

        def add_profile(uid, version, chunks):
            s.add(Profile(user_id=uid, version=version, data={}, content_hash="h"))
            for k, (emb, mv) in enumerate(chunks):
                s.add(ProfileChunk(
                    user_id=uid, profile_version=version, source_ref=f"r{k}",
                    kind="note", text="t", content_hash="h",
                    embedding=emb, model_version=mv,
                ))
            s.commit()

        def add_match(uid, job_id, version, ai_score):
            s.add(Match(
                user_id=uid, job_id=job_id, profile_version=version,
                vec_score=0.0, ai_score=ai_score, content_hash="h",
                state="ok" if ai_score is not None else "pendente",
            ))
            s.commit()

        yield s, new_user, add_job, add_profile, add_match
        s.rollback()
        for uid in created:
            s.execute(delete(Match).where(Match.user_id == uid))
            s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


db = pytest.mark.db
F = SearchFilters()


@db
def test_vec_score_is_mean_of_top3_normalized_cosine(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    # sims to job e0: e0 -> 1.0, mix(0,1) -> 1 - (1-1/sqrt2)/2, e1 -> 0.5,
    # e2 -> 0.5, -e0 -> 0.0 (excluded from top 3)
    add_profile(uid, 1, [
        (e(0), MV), (mix(0, 1), MV), (e(1), MV), (e(2), MV), (e(0, -1.0), MV),
    ])
    jid = add_job(uid, cid, embedding=e(0))
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.vectorized is True
    assert page.profile_version == 1
    assert page.chunks_used == 5
    assert page.total == 1
    [item] = page.items
    assert item.job.id == jid
    s_mix = 1 - (1 - 2 ** -0.5) / 2
    assert item.vec_score == pytest.approx((1.0 + s_mix + 0.5) / 3, abs=1e-6)
    assert item.match is None


@db
def test_fewer_than_three_chunks_uses_available(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV), (e(1), MV)])
    add_job(uid, cid, embedding=e(0))
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.chunks_used == 2
    assert page.items[0].vec_score == pytest.approx(0.75, abs=1e-6)


@db
def test_job_without_embedding_excluded_and_counted(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    ok = add_job(uid, cid, embedding=e(0))
    add_job(uid, cid)  # no embedding
    add_job(uid, cid, embedding=e(0), model_version="old-model")
    page = rank_jobs(s, uid, F, model_version=MV)
    assert [i.job.id for i in page.items] == [ok]
    assert page.total == 1
    assert page.missing_embeddings == 2


@db
def test_chunks_of_other_model_version_ignored(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), "old-model"), (e(1), MV), (None, None)])
    add_job(uid, cid, embedding=e(0))
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.chunks_used == 1
    assert page.items[0].vec_score == pytest.approx(0.5, abs=1e-6)

    # only old-model chunks -> not vectorized
    uid2, cid2 = new_user()
    add_profile(uid2, 1, [(e(0), "old-model"), (None, None)])
    add_job(uid2, cid2, embedding=e(0))
    page2 = rank_jobs(s, uid2, F, model_version=MV)
    assert page2.vectorized is False
    assert page2.items == []
    assert page2.chunks_used == 0
    assert page2.profile_version == 1


@db
def test_no_profile_means_not_vectorized_and_empty(env):
    s, new_user, add_job, _, _ = env
    uid, cid = new_user()
    add_job(uid, cid, embedding=e(0))
    add_job(uid, cid)
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.vectorized is False
    assert page.items == []
    assert page.total == 0
    assert page.profile_version is None
    assert page.chunks_used == 0
    assert page.missing_embeddings == 1
    assert top_candidate_ids(s, uid, F, model_version=MV, n=10) == []


@db
def test_only_current_profile_version_chunks_used(env):
    s, new_user, add_job, add_profile, add_match = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV), (e(0), MV), (e(0), MV)])
    add_profile(uid, 2, [(e(1), MV)])
    jid = add_job(uid, cid, embedding=e(0))
    add_match(uid, jid, 1, 90)  # stale version, must not be attached
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.profile_version == 2
    assert page.chunks_used == 1
    assert page.items[0].vec_score == pytest.approx(0.5, abs=1e-6)
    assert page.items[0].match is None
    add_match(uid, jid, 2, 70)
    s.expire_all()
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.items[0].match is not None
    assert page.items[0].match.profile_version == 2
    assert page.items[0].match.ai_score == 70


@db
def test_orders_by_ai_score_then_vec_score_then_id(env):
    s, new_user, add_job, add_profile, add_match = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    hi_vec = add_job(uid, cid, embedding=e(0))           # 1.0, no ai
    lo_vec_ai = add_job(uid, cid, embedding=e(1))        # 0.5, ai 80
    mid_vec_ai = add_job(uid, cid, embedding=mix(0, 1))  # ~0.85, ai 80
    ai_low = add_job(uid, cid, embedding=e(2))           # 0.5, ai 10
    tie_a = add_job(uid, cid, embedding=e(3))            # 0.5, no ai
    tie_b = add_job(uid, cid, embedding=e(4))            # 0.5, no ai
    pending = add_job(uid, cid, embedding=e(0, -1.0))    # 0.0, pending (null ai)
    add_match(uid, lo_vec_ai, 1, 80)
    add_match(uid, mid_vec_ai, 1, 80)
    add_match(uid, ai_low, 1, 10)
    add_match(uid, pending, 1, None)
    page = rank_jobs(s, uid, F, model_version=MV)
    assert [i.job.id for i in page.items] == [
        mid_vec_ai, lo_vec_ai, ai_low, hi_vec, tie_a, tie_b, pending,
    ]


@db
def test_pagination_is_stable_and_total_matches_filters(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    remote = [add_job(uid, cid, embedding=e(i + 1), work_model="remote") for i in range(5)]
    add_job(uid, cid, embedding=e(0), work_model="onsite")
    f = SearchFilters(work_models=["remote"], include_unknown=False)
    full = rank_jobs(s, uid, f, model_version=MV, limit=50)
    assert full.total == 5
    assert [i.job.id for i in full.items] == sorted(remote)
    pages = [
        rank_jobs(s, uid, f, model_version=MV, limit=2, offset=o) for o in (0, 2, 4)
    ]
    assert all(p.total == 5 for p in pages)
    assert [i.job.id for p in pages for i in p.items] == sorted(remote)


@db
def test_other_users_jobs_never_returned(env):
    s, new_user, add_job, add_profile, add_match = env
    uid, cid = new_user()
    other, ocid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    add_profile(other, 1, [(e(1), MV), (e(1), MV), (e(1), MV)])
    mine = add_job(uid, cid, embedding=e(1))
    theirs = add_job(other, ocid, embedding=e(0))
    add_match(other, theirs, 1, 99)
    page = rank_jobs(s, uid, F, model_version=MV)
    assert [i.job.id for i in page.items] == [mine]
    assert page.total == 1
    assert page.chunks_used == 1
    # user's own chunks are used, not the other user's (e1 would give 1.0)
    assert page.items[0].vec_score == pytest.approx(0.5, abs=1e-6)
    assert page.items[0].match is None
    ids = [j for j, _ in top_candidate_ids(s, uid, F, model_version=MV, n=10)]
    assert ids == [mine]


@db
def test_status_all_includes_closed(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    op = add_job(uid, cid, embedding=e(0))
    cl = add_job(uid, cid, embedding=e(1), status="closed")
    add_job(uid, cid, status="closed")  # closed, no embedding
    assert [i.job.id for i in rank_jobs(s, uid, F, model_version=MV).items] == [op]
    closed = rank_jobs(s, uid, F, model_version=MV, status="closed")
    assert [i.job.id for i in closed.items] == [cl]
    assert closed.missing_embeddings == 1
    every = rank_jobs(s, uid, F, model_version=MV, status="all")
    assert [i.job.id for i in every.items] == [op, cl]
    assert every.total == 2
    with pytest.raises(ValueError):
        rank_jobs(s, uid, F, model_version=MV, status="bogus")


@db
def test_top_candidate_ids_respects_filters_and_n(env):
    s, new_user, add_job, add_profile, add_match = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    best = add_job(uid, cid, embedding=e(0), country="PT")
    second = add_job(uid, cid, embedding=mix(0, 1), country="PT")
    third = add_job(uid, cid, embedding=e(1), country="PT")
    add_job(uid, cid, embedding=e(0), country="DE")
    add_job(uid, cid, embedding=e(0), country="PT", status="closed")
    # ai_score must not affect candidate order
    add_match(uid, third, 1, 100)
    f = SearchFilters(countries=["PT"], include_unknown=False)
    got = top_candidate_ids(s, uid, f, model_version=MV, n=2)
    assert [j for j, _ in got] == [best, second]
    assert got[0][1] == pytest.approx(1.0, abs=1e-6)
    assert got[1][1] == pytest.approx(1 - (1 - 2 ** -0.5) / 2, abs=1e-6)
    all3 = top_candidate_ids(s, uid, f, model_version=MV, n=10)
    assert [j for j, _ in all3] == [best, second, third]


def _finite_unit(x: float) -> bool:
    return math.isfinite(x) and 0.0 <= x <= 1.0


@db
def test_zero_vector_chunk_ignored_and_scores_finite(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV), ([0.0] * DIM, MV)])
    a = add_job(uid, cid, embedding=e(0))
    b = add_job(uid, cid, embedding=e(1))
    page = rank_jobs(s, uid, F, model_version=MV)
    assert page.chunks_used == 1
    assert [(i.job.id, i.vec_score) for i in page.items] == [
        (a, pytest.approx(1.0, abs=1e-6)), (b, pytest.approx(0.5, abs=1e-6)),
    ]
    assert all(_finite_unit(i.vec_score) for i in page.items)
    got = top_candidate_ids(s, uid, F, model_version=MV, n=10)
    assert all(_finite_unit(v) for _, v in got)

    # only zero chunks -> not vectorized
    uid2, cid2 = new_user()
    add_profile(uid2, 1, [([0.0] * DIM, MV)])
    add_job(uid2, cid2, embedding=e(0))
    page2 = rank_jobs(s, uid2, F, model_version=MV)
    assert page2.vectorized is False and page2.items == []


@db
def test_zero_vector_job_excluded_and_counted_missing(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    ok = add_job(uid, cid, embedding=e(1))
    add_job(uid, cid, embedding=[0.0] * DIM)
    page = rank_jobs(s, uid, F, model_version=MV)
    assert [i.job.id for i in page.items] == [ok]
    assert page.total == 1
    assert page.missing_embeddings == 1
    assert all(_finite_unit(i.vec_score) for i in page.items)
    assert [j for j, _ in top_candidate_ids(s, uid, F, model_version=MV, n=10)] == [ok]


def test_invalid_limit_offset_n_rejected():
    with pytest.raises(ValueError):
        rank_jobs(None, 1, F, model_version=MV, limit=0)
    with pytest.raises(ValueError):
        rank_jobs(None, 1, F, model_version=MV, offset=-1)
    with pytest.raises(ValueError):
        top_candidate_ids(None, 1, F, model_version=MV, n=0)


@db
def test_page_items_defer_job_embedding(env):
    s, new_user, add_job, add_profile, _ = env
    uid, cid = new_user()
    add_profile(uid, 1, [(e(0), MV)])
    add_job(uid, cid, embedding=e(0))
    s.expunge_all()
    page = rank_jobs(s, uid, F, model_version=MV)
    assert "embedding" not in page.items[0].job.__dict__
