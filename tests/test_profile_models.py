import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DataError, IntegrityError, StatementError

from rumo_core.db import SessionLocal
from rumo_core.models import Profile, ProfileChunk, ProfileImport

pytestmark = pytest.mark.db


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s
        s.rollback()
        for model in (ProfileChunk, Profile, ProfileImport):
            s.execute(delete(model))
        s.commit()


def _profile(version=1, content_hash="h1"):
    return Profile(
        user_id=1, version=version, data={"skills": []}, content_hash=content_hash
    )


def _chunk(embedding=None):
    return ProfileChunk(
        user_id=1, profile_version=1, source_ref="skill-1", kind="skill",
        text="Python", content_hash="c1", embedding=embedding,
    )


def test_profile_tables_roundtrip(session):
    session.add_all([
        ProfileImport(
            user_id=1, filename="cv.pdf", content_type="application/pdf",
            raw_text="texto", status="pending",
        ),
        Profile(
            user_id=1, version=1, data={"skills": ["python"]},
            narrative="resumo", content_hash="abc",
        ),
        _chunk(),
    ])
    session.commit()
    session.expire_all()

    imp = session.scalars(select(ProfileImport)).one()
    assert imp.filename == "cv.pdf"
    assert imp.status == "pending"
    assert imp.truncated is False
    assert imp.extracted is None and imp.error is None
    assert imp.created_at is not None and imp.updated_at is not None

    prof = session.scalars(select(Profile)).one()
    assert prof.data == {"skills": ["python"]}
    assert prof.narrative == "resumo"

    chunk = session.scalars(select(ProfileChunk)).one()
    assert chunk.embedding is None and chunk.model_version is None
    assert chunk.source_ref == "skill-1"


def test_profile_unique_version_per_user(session):
    session.add(_profile(version=1, content_hash="a"))
    session.commit()
    session.add(_profile(version=1, content_hash="b"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_same_content_hash_allowed_across_versions(session):
    session.add_all([_profile(1, "same"), _profile(2, "same")])
    session.commit()
    assert len(session.scalars(select(Profile)).all()) == 2


def test_chunk_vector_dimension(session):
    session.add(_chunk(embedding=[0.1] * 1024))
    session.commit()
    session.expire_all()
    assert len(session.scalars(select(ProfileChunk)).one().embedding) == 1024

    session.delete(session.scalars(select(ProfileChunk)).one())
    session.commit()
    session.add(_chunk(embedding=[0.1, 0.2, 0.3]))
    with pytest.raises((StatementError, DataError, ValueError)):
        session.flush()
