import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, SearchProfile, User
from rumo_core.sources.base import RawJob
from worker.collect_jobs import collect_companies

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def raw(eid, loc):
    return RawJob(
        external_id=str(eid), title=f"Engineer {eid}", url=f"https://example.test/{eid}",
        location_text=loc, description_html="<p>Python.</p>", department="Eng",
        posted_at=None, raw={"id": eid},
    )


class Src:
    name = "fake"

    def __init__(self, items):
        self.items = items

    async def fetch_jobs(self, identifier):
        return list(self.items)

    def normalize_identifier(self, text):
        return text.lower()

    async def aclose(self):
        pass


class Sleeps:
    async def __call__(self, s):
        return None


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"worker-funnel-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(SearchProfile).where(SearchProfile.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def _company(uid, mode="funnel"):
    with SessionLocal() as s:
        c = Company(user_id=uid, source="greenhouse", identifier="acme", name="Acme",
                    collect_mode=mode)
        s.add(c)
        s.commit()
        return c.id


def _profile(uid, filters):
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name="Padrão", is_default=True, filters=filters))
        s.commit()


async def _collect(uid, items):
    await collect_companies(
        SessionLocal, source_getter=lambda name: Src(items), user_id=uid,
        clock=lambda: NOW, sleep=Sleeps(), interval_hours=6, delay_seconds=0,
    )


def _intakes(cid):
    with SessionLocal() as s:
        return {j.external_id: j.intake for j in s.scalars(select(Job).where(Job.company_id == cid))}


async def test_funnel_company_stubs_jobs_failing_saved_filters(uid):
    _profile(uid, {"work_models": ["onsite"], "include_unknown": False})
    cid = _company(uid)
    await _collect(uid, [raw(1, "Remote, Brazil"), raw(2, "Berlin, Germany")])
    assert _intakes(cid) == {"1": "stub", "2": "stub"}


async def test_invalid_saved_filters_disable_funnel(uid):
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=uid, name="Padrão", is_default=True,
                            filters={"countries": 5}))
        s.commit()
    cid = _company(uid)
    await _collect(uid, [raw(1, "Remote, Brazil"), raw(2, "Berlin, Germany")])
    assert set(_intakes(cid).values()) == {"full"}


async def test_collect_mode_all_keeps_everything_full(uid):
    _profile(uid, {"work_models": ["onsite"], "include_unknown": False})
    cid = _company(uid, mode="all")
    await _collect(uid, [raw(1, "Remote, Brazil")])
    assert _intakes(cid) == {"1": "full"}
