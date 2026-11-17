import uuid

import pytest
from sqlalchemy import delete

from rumo_core.applications.select import select_evidence
from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.models import ProfileChunk, User

pytestmark = pytest.mark.db


def _basis(i: int, scale: float = 1.0) -> list[float]:
    v = [0.0] * 1024
    v[i] = scale
    return v


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"select-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uids"] = [user.id]
        yield s
        s.rollback()
        s.execute(delete(ProfileChunk).where(ProfileChunk.user_id.in_(s.info["uids"])))
        s.execute(delete(User).where(User.id.in_(s.info["uids"])))
        s.commit()


def _chunk(uid, version, ref, vec, model=MODEL_VERSION):
    return ProfileChunk(
        user_id=uid, profile_version=version, source_ref=ref, kind="experience",
        text=ref, content_hash=ref, embedding=vec, model_version=model,
    )


def test_select_returns_two_nearest_per_requirement(session):
    uid = session.info["uids"][0]
    session.add_all([
        _chunk(uid, 1, "exp_a", _basis(0)),
        _chunk(uid, 1, "exp_b", _basis(1)),
        _chunk(uid, 1, "exp_c", _basis(2)),
    ])
    session.commit()
    out = select_evidence(
        session, uid, 1, MODEL_VERSION,
        {"m1": _basis(0, 0.9), "m2": _basis(1, 0.8)}, k=2,
    )
    assert out["m1"][0] == "exp_a" and len(out["m1"]) == 2
    assert out["m2"][0] == "exp_b"


def test_select_ignores_other_version_model_and_user(session):
    from rumo_core.models import User as _User

    uid = session.info["uids"][0]
    other = _User(email=f"select-other-{uuid.uuid4().hex}@example.test")
    session.add(other)
    session.commit()
    session.info["uids"].append(other.id)
    session.add_all([
        _chunk(uid, 2, "old_version", _basis(0)),
        _chunk(uid, 1, "other_model", _basis(0), model="x-other"),
        _chunk(other.id, 1, "other_user", _basis(0)),
        _chunk(uid, 1, "mine", _basis(3)),
    ])
    session.commit()
    out = select_evidence(session, uid, 1, MODEL_VERSION, {"m1": _basis(0)}, k=2)
    assert out["m1"] == ["mine"]
