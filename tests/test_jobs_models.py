import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, User

pytestmark = pytest.mark.db


def _cleanup(s, uids):
    s.rollback()
    s.execute(delete(Job).where(Job.user_id.in_(uids)))
    s.execute(delete(Company).where(Company.user_id.in_(uids)))
    s.execute(delete(User).where(User.id.in_(uids)))
    s.commit()


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"jobs-models-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        s.info["uids"] = [user.id]
        yield s
        _cleanup(s, s.info["uids"])


def _company(uid, identifier="acme"):
    return Company(
        user_id=uid, source="greenhouse", identifier=identifier, name="Acme"
    )


def _job(uid, company_id, external_id="1", **kw):
    return Job(
        user_id=uid, company_id=company_id, external_id=external_id,
        url="https://example.test/j/1", title="Engineer", description="desc",
        raw={"id": 1}, content_hash="h", fingerprint="f", **kw,
    )


def test_company_job_roundtrip(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.flush()
    session.add(_job(uid, c.id))
    session.commit()
    session.expire_all()

    c = session.scalars(select(Company).where(Company.user_id == uid)).one()
    assert c.active is True
    assert c.consecutive_failures == 0 and c.open_jobs == 0
    assert c.country is None and c.last_run_at is None
    assert c.last_success_at is None and c.next_run_at is None
    assert c.last_error is None

    j = session.scalars(select(Job).where(Job.user_id == uid)).one()
    assert j.company_id == c.id
    assert j.work_model == "unknown"
    assert j.seniority == "unknown"
    assert j.language == "unknown"
    assert j.status == "open"
    assert j.salary_known is False
    assert j.fill_attempts == 0
    assert j.location_text == ""
    assert j.raw == {"id": 1}
    assert j.first_seen_at is not None and j.last_seen_at is not None
    assert j.closed_at is None and j.posted_at is None
    assert j.salary_min is None and j.salary_ref_min is None
    assert j.llm_filled_at is None and j.embedding is None
    assert j.model_version is None
    assert j.created_at is not None and j.updated_at is not None


def test_company_unique_per_user_source_identifier(session):
    uid = session.info["uid"]
    session.add(_company(uid))
    session.commit()
    session.add(_company(uid))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    other = User(email=f"jobs-models-{uuid.uuid4().hex}@example.test")
    session.add(other)
    session.commit()
    session.info["uids"].append(other.id)
    session.add(_company(other.id))
    session.commit()


def test_job_unique_per_company_external_id(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.flush()
    session.add(_job(uid, c.id, "x"))
    session.commit()
    session.add(_job(uid, c.id, "x"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_job_vector_dimension(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.flush()
    session.add(_job(uid, c.id, "ok", embedding=[0.1] * 1024))
    session.commit()
    session.add(_job(uid, c.id, "bad", embedding=[0.1, 0.2, 0.3]))
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()


def test_cascade_delete_company_removes_jobs(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.flush()
    session.add_all([_job(uid, c.id, "a"), _job(uid, c.id, "b")])
    session.commit()
    session.delete(session.get(Company, c.id))
    session.commit()
    session.expire_all()
    assert session.scalars(select(Job).where(Job.user_id == uid)).all() == []
