import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from rumo_core.config import Settings
from rumo_core.db import SessionLocal
from rumo_core.models import Company, DiscoveryCandidate, User, UserPreference


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"discovery-models-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        yield s
        s.rollback()
        uid = user.id
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid))
        s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
        s.execute(delete(Company).where(Company.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _company(uid, **kw):
    return Company(
        user_id=uid, source="greenhouse", identifier="acme", name="Acme", **kw
    )


def _cand(uid, token="acme", **kw):
    return DiscoveryCandidate(
        user_id=uid, source="greenhouse", token=token, origin_kind="catalog", **kw
    )


@pytest.mark.db
def test_company_origin_defaults_manual_and_checks_values(session):
    uid = session.info["uid"]
    c = _company(uid)
    session.add(c)
    session.commit()
    session.refresh(c)
    assert c.origin == "manual"
    c.origin = "auto"
    session.commit()
    c.origin = "bogus"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.db
def test_candidate_unique_per_user_source_token(session):
    uid = session.info["uid"]
    session.add(_cand(uid))
    session.commit()
    session.add(_cand(uid))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(_cand(uid, token="other"))
    session.commit()


@pytest.mark.db
def test_candidate_state_check(session):
    uid = session.info["uid"]
    cand = _cand(uid)
    session.add(cand)
    session.commit()
    session.refresh(cand)
    assert cand.state == "pending" and cand.attempts == 0
    assert cand.name_hint is None and cand.retry_after is None
    assert cand.last_error is None and cand.company_id is None
    cand.state = "bogus"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    cand = session.get(DiscoveryCandidate, cand.id)
    cand.origin_kind = "bogus"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.db
def test_candidate_company_set_null_on_company_delete(session):
    uid = session.info["uid"]
    c = _company(uid, origin="auto")
    session.add(c)
    session.flush()
    cand = _cand(uid, company_id=c.id, state="valid")
    session.add(cand)
    session.commit()
    cid = cand.id
    session.delete(session.get(Company, c.id))
    session.commit()
    session.expire_all()
    cand = session.get(DiscoveryCandidate, cid)
    assert cand is not None and cand.company_id is None


@pytest.mark.db
def test_user_preference_defaults(session):
    uid = session.info["uid"]
    session.add(UserPreference(user_id=uid))
    session.commit()
    session.expire_all()
    pref = session.scalars(
        select(UserPreference).where(UserPreference.user_id == uid)
    ).one()
    assert pref.discovery_enabled is True
    assert pref.target_companies == []
    assert pref.updated_at is not None


def test_settings_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", _env_file=None)
    assert s.discovery_batch == 5
    assert s.discovery_max_seconds == 20
    assert s.discovery_max_auto_companies == 100
    assert s.discovery_max_attempts == 3
    for name in (
        "discovery_batch", "discovery_max_seconds",
        "discovery_max_auto_companies", "discovery_max_attempts",
    ):
        with pytest.raises(ValueError):
            Settings(
                database_url="postgresql+psycopg://x/y", _env_file=None,
                **{name: 0},
            )
