import uuid

import pytest
from sqlalchemy import delete

from rumo_core.db import SessionLocal
from rumo_core.matching.gate import apply_vec_gate
from rumo_core.matching.quality import floor_from_scores, suggest_floor
from rumo_core.models import Company, Job, JobLabel, Profile, ProfileChunk, User, UserPreference

MV = "bge-m3@test"
DIM = 1024


def e(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


@pytest.fixture
def world():
    with SessionLocal() as s:
        u = User(email=f"gate-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
        s.add(c)
        s.flush()
        s.add(Profile(user_id=u.id, version=1, data={}, content_hash="h"))
        s.add(ProfileChunk(user_id=u.id, profile_version=1, source_ref="r0", kind="note",
                           text="t", content_hash="h", embedding=e(0), model_version=MV))
        s.commit()
        uid, cid = u.id, c.id
    yield uid, cid
    with SessionLocal() as s:
        s.execute(delete(JobLabel).where(JobLabel.user_id == uid))
        s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
        s.execute(delete(Job).where(Job.user_id == uid))
        s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
        s.execute(delete(Profile).where(Profile.user_id == uid))
        s.execute(delete(Company).where(Company.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _job(uid, cid, ext, embedding, **kw):
    return Job(
        user_id=uid, company_id=cid, external_id=ext, url="https://example.test/j",
        title=f"t{ext}", description="d", raw={}, content_hash=f"h{ext}",
        fingerprint=f"f{ext}", embedding=embedding, model_version=MV, llm_filled_at=None,
        **kw,
    )


def _add(world, *jobs):
    uid, _ = world
    with SessionLocal() as s:
        rows = [_job(uid, world[1], *j[:2], **j[2]) for j in jobs]
        s.add_all(rows)
        s.commit()
        return {r.external_id: r.id for r in rows}


def _state(uid, ids):
    with SessionLocal() as s:
        return {
            ext: (j.intake, j.llm_filled_at is not None)
            for ext, j in ((e_, s.get(Job, jid)) for e_, jid in ids.items())
        }


def test_gate_moves_below_floor_to_gated_and_back(world):
    uid, _ = world
    ids = _add(world, ("a", e(0), {}), ("b", e(1), {}))
    with SessionLocal() as s:
        res = apply_vec_gate(s, uid, model_version=MV, floor=0.75)
        s.commit()
    assert (res.gated, res.restored) == (1, 0)
    assert _state(uid, ids)["a"][0] == "full" and _state(uid, ids)["b"][0] == "gated"
    with SessionLocal() as s:
        res = apply_vec_gate(s, uid, model_version=MV, floor=None)
        s.commit()
    assert res.restored == 1
    assert _state(uid, ids)["b"] == ("full", False)


def test_gated_keeps_vector(world):
    uid, _ = world
    ids = _add(world, ("b", e(1), {}))
    with SessionLocal() as s:
        apply_vec_gate(s, uid, model_version=MV, floor=0.75)
        s.commit()
    with SessionLocal() as s:
        assert s.get(Job, ids["b"]).embedding is not None


def test_gate_skips_collect_mode_all(world):
    uid, _ = world
    with SessionLocal() as s:
        c = Company(user_id=uid, source="lever", identifier="zeta", name="Zeta",
                    collect_mode="all")
        s.add(c)
        s.flush()
        s.add(_job(uid, c.id, "z", e(1)))
        s.commit()
    with SessionLocal() as s:
        res = apply_vec_gate(s, uid, model_version=MV, floor=0.75)
        s.commit()
    assert res.gated == 0


def test_gate_without_profile_does_nothing(world):
    uid, _ = world
    _add(world, ("b", e(1), {}))
    with SessionLocal() as s:
        s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
        s.commit()
    with SessionLocal() as s:
        assert apply_vec_gate(s, uid, model_version=MV, floor=0.75).gated == 0


def test_floor_from_scores_rules():
    scores = [0.50 + i / 100 for i in range(20)]  # 0.50 .. 0.69
    assert floor_from_scores(scores) == 0.51
    assert floor_from_scores([0.5] * 9) is None
    assert floor_from_scores([i / 100 for i in range(40)]) == 0.02
    assert floor_from_scores([0.6789] * 10) == 0.678


def test_suggest_floor_needs_ten_good_labels(world):
    uid, cid = world
    ids = _add(world, *[(f"g{i}", e(0), {}) for i in range(9)])
    with SessionLocal() as s:
        for jid in ids.values():
            s.add(JobLabel(user_id=uid, job_id=jid, label="good"))
        s.commit()
    with SessionLocal() as s:
        assert suggest_floor(s, uid, model_version=MV) is None
    more = _add(world, ("g9", e(0), {}))
    with SessionLocal() as s:
        s.add(JobLabel(user_id=uid, job_id=more["g9"], label="good"))
        s.commit()
    with SessionLocal() as s:
        assert suggest_floor(s, uid, model_version=MV) == 1.0


def test_fill_waits_for_embedding_when_floor_set(world):
    from worker.collect_jobs import _select_fill_candidates
    from rumo_core.models import UserPreference

    uid, cid = world
    with SessionLocal() as s:
        s.add(UserPreference(user_id=uid, adherence_vec_floor=0.9))
        s.add_all([
            _job(uid, cid, "nov", None, work_model="unknown"),
            _job(uid, cid, "vec", e(0), work_model="unknown"),
        ])
        s.commit()
    picked, _ = _select_fill_candidates(SessionLocal, batch=10, user_id=uid)
    with SessionLocal() as s:
        names = {s.get(Job, jid).external_id for jid, _ in picked}
    assert names == {"vec"}
