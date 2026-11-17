import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from rumo_core.db import SessionLocal
from rumo_core.models import (
    Company,
    DiscoveryCandidate,
    Job,
    JobFeed,
    PortalState,
    User,
    UserPreference,
)

pytestmark = pytest.mark.db


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"funnel-models-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        yield s
        s.rollback()
        uid = user.id
        s.execute(delete(JobFeed).where(JobFeed.user_id == uid))
        s.execute(delete(PortalState).where(PortalState.user_id == uid))
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid))
        s.execute(delete(Job).where(Job.user_id == uid))
        s.execute(delete(Company).where(Company.user_id == uid))
        s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _company(uid, source="greenhouse", identifier="acme", **kw):
    return Company(user_id=uid, source=source, identifier=identifier, name="Acme", **kw)


def _job(uid, cid, external_id="1", **kw):
    return Job(
        user_id=uid, company_id=cid, external_id=external_id,
        url="https://example.test/j/1", title="Engineer", description="desc",
        raw={"id": 1}, content_hash="h", fingerprint="f", **kw,
    )


def test_job_defaults_full_ats_rank0(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.flush()
    session.add(_job(uid, c.id))
    session.commit()
    session.expire_all()
    job = session.query(Job).filter_by(user_id=uid).one()
    assert job.intake == "full"
    assert job.origin == "ats"
    assert job.source_rank == 0
    assert job.stub_reason is None
    assert session.get(Company, c.id).collect_mode == "funnel"


def test_job_intake_and_origin_checks(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.commit()
    cid = c.id
    session.add(_job(uid, cid, intake="x"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(_job(uid, cid, origin="rss"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_company_collect_mode_and_origin(session):
    uid = session.info["uid"]
    session.add(_company(uid, identifier="a", collect_mode="tudo"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(_company(uid, identifier="b", origin="feed"))
    session.add(_company(uid, identifier="c", origin="link"))
    session.commit()
    with pytest.raises(IntegrityError):
        session.add(_company(uid, identifier="d", origin="rss"))
        session.commit()
    session.rollback()


def test_job_feed_unique_per_params(session):
    uid = session.info["uid"]
    session.add(JobFeed(user_id=uid, source="remotive", params={"keywords": ["dados"]}))
    session.commit()
    session.add(JobFeed(user_id=uid, source="remotive", params={"keywords": ["dados"]}))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(JobFeed(user_id=uid, source="remotive", params={"keywords": ["py"]}))
    session.add(JobFeed(user_id=uid, source="remoteok", params={"keywords": ["dados"]}))
    session.commit()


def test_portal_state_unique(session):
    uid = session.info["uid"]
    session.add(PortalState(user_id=uid, portal="vagas", enabled=True))
    session.commit()
    session.add(PortalState(user_id=uid, portal="vagas"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_discovery_candidate_accepts_feed_origin(session):
    uid = session.info["uid"]
    session.add(DiscoveryCandidate(user_id=uid, source="lever", token="acme", origin_kind="feed"))
    session.commit()


def test_adherence_vec_floor_nullable(session):
    uid = session.info["uid"]
    session.add(UserPreference(user_id=uid))
    session.commit()
    session.expire_all()
    assert session.get(UserPreference, uid).adherence_vec_floor is None


def test_migration_head_is_single(session):
    rows = session.execute(text("select version_num from alembic_version")).all()
    assert [r[0] for r in rows] == ["0014"]
