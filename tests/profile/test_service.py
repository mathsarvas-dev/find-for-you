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


def _noid(job2="Lead"):
    return CanonicalProfile.model_validate({
        "summary": "Dev",
        "experiences": [
            {"company": "Acme", "title": "Dev",
             "achievements": [{"text": "a"}]},
            {"company": "Beta", "title": job2},
        ],
        "skills": [{"name": "Python"}],
    })


def test_empty_ids_confirm_twice_is_one_version(session):
    uid = session.info["uids"][0]
    a = confirm_profile(session, uid, _noid(), None)
    b = confirm_profile(session, uid, _noid(), None)
    assert a.id == b.id
    chunks = _chunks(session, uid, 1)
    assert all(ref for ref in chunks)
    assert len(chunks) == 4


def test_edit_with_empty_ids_keeps_other_ids_and_embeddings(session):
    uid = session.info["uids"][0]
    confirm_profile(session, uid, _noid(), None)
    c1 = _chunks(session, uid, 1)
    for c in c1.values():
        c.embedding = [0.1] * 1024
        c.model_version = "m1"
    session.commit()
    row = confirm_profile(session, uid, _noid(job2="Diretor"), None)
    assert row.version == 2
    c2 = _chunks(session, uid, 2)
    assert set(c2) - set(c1) and len(set(c2) & set(c1)) == 3
    changed = (set(c2) - set(c1)).pop()
    assert c2[changed].embedding is None
    for ref in set(c2) & set(c1):
        assert c2[ref].embedding is not None


def test_race_lost_returns_existing_without_duplicate(session, monkeypatch):
    from rumo_core.profile import service
    uid = session.info["uids"][0]
    existing = confirm_profile(session, uid, _profile(), None)
    real = service.get_current_profile
    calls = []

    def stale(s, u):
        calls.append(1)
        return None if len(calls) == 1 else real(s, u)

    monkeypatch.setattr(service, "get_current_profile", stale)
    row = confirm_profile(session, uid, _profile(), None)
    assert row.id == existing.id
    rows = session.scalars(select(Profile).where(Profile.user_id == uid)).all()
    assert len(rows) == 1


def test_value_error_rolls_back_pending_profile(session, monkeypatch):
    from rumo_core.profile import service
    uid = session.info["uids"][0]

    def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(service, "build_chunks", boom)
    with pytest.raises(ValueError):
        confirm_profile(session, uid, _profile(), None)
    assert not session.new
    assert get_current_profile(session, uid) is None


def _bad_ids():
    return CanonicalProfile.model_validate({
        "summary": "Dev",
        "experiences": [
            {"id": "1", "company": "Acme", "title": "Dev"},
            {"id": "summary", "company": "Beta", "title": "Lead"},
            {"id": "x" * 300, "company": "Gama", "title": "CTO"},
        ],
        "education": [{"id": "1", "institution": "USP"}],
        "skills": [{"id": "skills", "name": "Python"}],
    })


def test_confirm_reids_duplicate_reserved_and_overlong_ids(session):
    uid = session.info["uids"][0]
    row = confirm_profile(session, uid, _bad_ids(), None)
    chunks = _chunks(session, uid, row.version)
    assert len(chunks) == 6  # summary, 3 experiences, education, skills
    refs = set(chunks)
    assert "1" in refs  # first occurrence is kept
    data = row.data
    ids = [i["id"] for sec in ("experiences", "education", "skills") for i in data[sec]]
    assert len(ids) == len(set(ids))
    assert all(i and i not in ("summary", "skills") and len(i) <= 64 for i in ids)


def test_confirm_with_bad_ids_is_idempotent_on_double_submit(session):
    uid = session.info["uids"][0]
    a = confirm_profile(session, uid, _bad_ids(), None)
    b = confirm_profile(session, uid, _bad_ids(), None)
    c = confirm_profile(session, uid, CanonicalProfile.model_validate(a.data), None)
    assert a.id == b.id == c.id
    rows = session.scalars(select(Profile).where(Profile.user_id == uid)).all()
    assert len(rows) == 1
