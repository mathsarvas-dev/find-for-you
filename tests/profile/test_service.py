import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.models import Profile, ProfileChunk, User
from rumo_core.profile.schema import CanonicalProfile, assign_ids
from rumo_core.profile.service import confirm_profile, get_current_profile

pytestmark = pytest.mark.db


@pytest.fixture
def session():
    with SessionLocal() as s:
        users = [User(email=f"svc-test-{i}@example.test") for i in (1, 2)]
        s.add_all(users)
        s.commit()
        ids = [u.id for u in users]
        s.info["uids"] = ids
        yield s
        s.rollback()
        s.execute(delete(ProfileChunk).where(ProfileChunk.user_id.in_(ids)))
        s.execute(delete(Profile).where(Profile.user_id.in_(ids)))
        s.execute(delete(User).where(User.id.in_(ids)))
        s.commit()


def _profile(job2="Lead"):
    return assign_ids(CanonicalProfile.model_validate({
        "summary": "Dev",
        "experiences": [
            {"id": "e1", "company": "Acme", "title": "Dev"},
            {"id": "e2", "company": "Beta", "title": job2},
        ],
        "skills": [{"id": "s1", "name": "Python"}],
    }))


def _chunks(session, uid, version):
    return {
        c.source_ref: c for c in session.scalars(select(ProfileChunk).where(
            ProfileChunk.user_id == uid, ProfileChunk.profile_version == version))
    }


def test_confirm_creates_version_1_with_pending_chunks(session):
    uid = session.info["uids"][0]
    row = confirm_profile(session, uid, _profile(), "narr")
    assert row.version == 1
    assert get_current_profile(session, uid).id == row.id
    chunks = _chunks(session, uid, 1)
    assert set(chunks) == {"summary", "e1", "e2", "skills"}
    assert all(c.embedding is None for c in chunks.values())


def test_confirm_twice_same_profile_is_idempotent(session):
    uid = session.info["uids"][0]
    a = confirm_profile(session, uid, _profile(), "n")
    b = confirm_profile(session, uid, _profile(), "n")
    assert a.id == b.id
    rows = session.scalars(select(Profile).where(Profile.user_id == uid)).all()
    assert len(rows) == 1
    assert len(_chunks(session, uid, 1)) == 4


def test_edit_creates_version_2_and_reuses_unchanged_embeddings(session):
    uid = session.info["uids"][0]
    confirm_profile(session, uid, _profile(), None)
    c1 = _chunks(session, uid, 1)
    for ref in ("e1", "e2"):
        c1[ref].embedding = [0.1] * 1024
        c1[ref].model_version = "m1"
    session.commit()
    row = confirm_profile(session, uid, _profile(job2="Diretor"), None)
    assert row.version == 2
    c2 = _chunks(session, uid, 2)
    assert c2["e1"].embedding is not None and c2["e1"].model_version == "m1"
    assert c2["e2"].embedding is None and c2["e2"].model_version is None


def test_revert_to_older_profile_creates_new_version(session):
    uid = session.info["uids"][0]
    confirm_profile(session, uid, _profile(), None)
    confirm_profile(session, uid, _profile(job2="Diretor"), None)
    row = confirm_profile(session, uid, _profile(), None)
    assert row.version == 3


def test_versions_are_per_user(session):
    u1, u2 = session.info["uids"]
    assert confirm_profile(session, u1, _profile(), None).version == 1
    assert confirm_profile(session, u2, _profile(), None).version == 1
    assert get_current_profile(session, u2).user_id == u2
