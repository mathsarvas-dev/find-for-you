import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from rumo_core.config import get_settings
from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, Match, SearchProfile, User

pytestmark = pytest.mark.db


def _cleanup(s, uids):
    s.rollback()
    s.execute(delete(Match).where(Match.user_id.in_(uids)))
    s.execute(delete(SearchProfile).where(SearchProfile.user_id.in_(uids)))
    s.execute(delete(Job).where(Job.user_id.in_(uids)))
    s.execute(delete(Company).where(Company.user_id.in_(uids)))
    s.execute(delete(User).where(User.id.in_(uids)))
    s.commit()


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"match-models-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        s.info["uids"] = [user.id]
        yield s
        _cleanup(s, s.info["uids"])


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


def _match(uid, job_id, **kw):
    kw.setdefault("profile_version", 1)
    kw.setdefault("vec_score", 0.5)
    kw.setdefault("content_hash", "h")
    return Match(user_id=uid, job_id=job_id, **kw)


def test_match_unique_per_job_and_version(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(_match(uid, j.id))
    session.commit()
    m = session.scalars(select(Match).where(Match.user_id == uid)).one()
    assert m.state == "pendente" and m.attempts == 0
    assert m.ai_score is None and m.scored_at is None
    session.add(_match(uid, j.id))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(_match(uid, j.id, profile_version=2))
    session.commit()


def test_match_rejects_bad_state_and_score(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(_match(uid, j.id, state="x"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(_match(uid, j.id, ai_score=101))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_only_one_default_search_profile_per_user(session):
    uid = session.info["uid"]
    session.add(SearchProfile(user_id=uid, name="a", is_default=True))
    session.add(SearchProfile(user_id=uid, name="b", is_default=False))
    session.add(SearchProfile(user_id=uid, name="c", is_default=False))
    session.commit()
    sp = session.scalars(
        select(SearchProfile).where(SearchProfile.name == "a")
    ).one()
    assert sp.filters == {} and sp.query_text is None
    assert sp.query_embedding is None
    session.add(SearchProfile(user_id=uid, name="d", is_default=True))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(SearchProfile(user_id=uid, name="a"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_match_deleted_with_job(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(_match(uid, j.id))
    session.commit()
    session.delete(session.get(Job, j.id))
    session.commit()
    session.expire_all()
    assert session.scalars(select(Match).where(Match.user_id == uid)).all() == []


def test_settings_defaults():
    s = get_settings()
    assert s.match_batch == 3
    assert s.match_top_n == 30
    assert s.match_weight_profile == 1.0
    assert s.match_weight_query == 0.0
    assert s.match_max_attempts == 3
