import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from rumo_core.db import SessionLocal
from rumo_core.models import (
    Company, Job, JobLabel, SearchProfile, User, UserPreference,
)

pytestmark = pytest.mark.db


def _cleanup(s, uids):
    s.rollback()
    s.execute(delete(JobLabel).where(JobLabel.user_id.in_(uids)))
    s.execute(delete(SearchProfile).where(SearchProfile.user_id.in_(uids)))
    s.execute(delete(UserPreference).where(UserPreference.user_id.in_(uids)))
    s.execute(delete(Job).where(Job.user_id.in_(uids)))
    s.execute(delete(Company).where(Company.user_id.in_(uids)))
    s.execute(delete(User).where(User.id.in_(uids)))
    s.commit()


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"job-labels-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        yield s
        _cleanup(s, [s.info["uid"]])


def _job(s, uid):
    c = Company(user_id=uid, source="greenhouse", identifier="acme", name="Acme")
    s.add(c)
    s.flush()
    j = Job(
        user_id=uid, company_id=c.id, external_id="1",
        url="https://example.test/j/1", title="Engineer", description="d",
        raw={"id": 1}, content_hash="h", fingerprint="f",
    )
    s.add(j)
    s.commit()
    return j


def test_job_label_unique_per_user_and_job(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(JobLabel(user_id=uid, job_id=j.id, label="good"))
    session.commit()
    session.add(JobLabel(user_id=uid, job_id=j.id, label="bad"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_job_label_rejects_other_values(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(JobLabel(user_id=uid, job_id=j.id, label="meh"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_job_label_deleted_with_job(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(JobLabel(user_id=uid, job_id=j.id, label="good"))
    session.commit()
    session.execute(delete(Job).where(Job.id == j.id))
    session.commit()
    assert session.scalars(
        select(JobLabel).where(JobLabel.user_id == uid)
    ).all() == []


def test_unaccent_available(session):
    got = session.scalar(text("select lower(unaccent('Plantão Ação'))"))
    assert got == "plantao acao"


def test_search_profile_query_model_version_and_quality_seed_nullable(session):
    uid = session.info["uid"]
    sp = SearchProfile(user_id=uid, name="p")
    pref = UserPreference(user_id=uid)
    session.add_all([sp, pref])
    session.commit()
    assert sp.query_model_version is None
    assert pref.quality_seed is None
    sp.query_model_version = "bge-m3"
    pref.quality_seed = 42
    session.commit()
    session.refresh(sp)
    session.refresh(pref)
    assert sp.query_model_version == "bge-m3"
    assert pref.quality_seed == 42
