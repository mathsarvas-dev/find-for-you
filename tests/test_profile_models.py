import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DataError, IntegrityError, StatementError

from rumo_core.db import SessionLocal
from rumo_core.models import Profile, ProfileChunk, ProfileImport, User

pytestmark = pytest.mark.db


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email="models-test@example.test")
        s.add(user)
        s.commit()
        uid = user.id
        s.info["uid"] = uid
        yield s
        s.rollback()
        for model in (ProfileChunk, Profile, ProfileImport):
            s.execute(delete(model).where(model.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _profile(uid, version=1, content_hash="h1"):
    return Profile(
        user_id=uid, version=version, data={"skills": []}, content_hash=content_hash
    )


def _chunk(uid, embedding=None):
    return ProfileChunk(
        user_id=uid, profile_version=1, source_ref="skill-1", kind="skill",
        text="Python", content_hash="c1", embedding=embedding,
    )


def test_profile_tables_roundtrip(session):
    uid = session.info["uid"]
    session.add_all([
        ProfileImport(
            user_id=uid, filename="cv.pdf", content_type="application/pdf",
            raw_text="texto", status="pending",
        ),
        Profile(
            user_id=uid, version=1, data={"skills": ["python"]},
            narrative="resumo", content_hash="abc",
        ),
        _chunk(uid),
    ])
    session.commit()
    session.expire_all()

    imp = session.scalars(select(ProfileImport).where(ProfileImport.user_id == uid)).one()
    assert imp.filename == "cv.pdf"
    assert imp.status == "pending"
    assert imp.truncated is False
    assert imp.extracted is None and imp.error is None
    assert imp.created_at is not None and imp.updated_at is not None

    prof = session.scalars(select(Profile).where(Profile.user_id == uid)).one()
    assert prof.data == {"skills": ["python"]}
    assert prof.narrative == "resumo"

    chunk = session.scalars(select(ProfileChunk).where(ProfileChunk.user_id == uid)).one()
    assert chunk.embedding is None and chunk.model_version is None
    assert chunk.source_ref == "skill-1"


def test_profile_unique_version_per_user(session):
    uid = session.info["uid"]
    session.add(_profile(uid, version=1, content_hash="a"))
    session.commit()
    session.add(_profile(uid, version=1, content_hash="b"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_same_content_hash_allowed_across_versions(session):
    uid = session.info["uid"]
    session.add_all([_profile(uid, 1, "same"), _profile(uid, 2, "same")])
    session.commit()
    assert len(session.scalars(select(Profile).where(Profile.user_id == uid)).all()) == 2


def test_chunk_vector_dimension(session):
    uid = session.info["uid"]
    session.add(_chunk(uid, embedding=[0.1] * 1024))
    session.commit()
    session.expire_all()
    assert len(session.scalars(select(ProfileChunk).where(ProfileChunk.user_id == uid)).one().embedding) == 1024

    session.delete(session.scalars(select(ProfileChunk).where(ProfileChunk.user_id == uid)).one())
    session.commit()
    session.add(_chunk(uid, embedding=[0.1, 0.2, 0.3]))
    with pytest.raises((StatementError, DataError, ValueError)):
        session.flush()
