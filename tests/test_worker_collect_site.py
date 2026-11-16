import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, PortalState, User
from rumo_core.sources.base import RawJob, SourceBlocked
from rumo_core.sources.careers import MSG_NO_STRUCTURED, EmbeddedAtsFound, NoStructuredData, SiteFetch
from rumo_core.sources.robots import RobotsDisallowed
from worker import collect_jobs as cj
from worker.collect_jobs import collect_companies

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
PAGE = "https://jobs.acme.test/careers"


def raw(eid):
    return RawJob(
        external_id=str(eid), title=f"Engineer {eid}", url=f"https://jobs.acme.test/jobs/{eid}",
        location_text="Remote, Brazil", description_html="<p>Python backend.</p>",
        department=None, posted_at=None, raw={"id": eid},
    )


class FakeSite:
    name = "site"

    def __init__(self, result):
        self.result = result  # SiteFetch | BaseException
        self.calls: list[tuple[str, frozenset[str]]] = []

    async def fetch_listing(self, url, *, known_ids):
        self.calls.append((url, known_ids))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def fetch_jobs(self, identifier):
        return (await self.fetch_listing(identifier, known_ids=frozenset())).raws

    def normalize_identifier(self, text):
        return text

    async def aclose(self):
        pass


class Sleeps:
    async def __call__(self, s):
        return None


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"worker-site-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(PortalState).where(PortalState.user_id == user_id))
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def add_company(uid, source, identifier, **kw):
    with SessionLocal() as s:
        c = Company(user_id=uid, source=source, identifier=identifier,
                    name="Acme Ltda", **kw)
        s.add(c)
        s.commit()
        return c.id


def add_job(uid, cid, eid, status="open"):
    with SessionLocal() as s:
        now = NOW
        s.add(Job(
            user_id=uid, company_id=cid, external_id=eid, title=f"Old {eid}",
            url=f"https://jobs.acme.test/jobs/{eid}", location_text="x", description="d",
            raw={}, status=status, first_seen_at=now, last_seen_at=now, fill_attempts=0,
            content_hash=f"h{eid}", fingerprint=f"f{eid}", department=None,
        ))
        s.commit()


def get_company(cid):
    with SessionLocal() as s:
        return s.get(Company, cid)


def job_rows(cid):
    with SessionLocal() as s:
        return list(s.scalars(select(Job).where(Job.company_id == cid).order_by(Job.external_id)))


async def collect(uid, source, *, now=NOW):
    # one company per run: a converted company is due again at once (next_run_at=now)
    return await collect_companies(
        SessionLocal, source_getter=lambda name: source, user_id=uid,
        clock=lambda: now, sleep=Sleeps(), interval_hours=6, delay_seconds=0,
        max_companies=1,
    )


async def test_site_company_ingests_listing_with_listed_ids(uid):
    cid = add_company(uid, "site", PAGE)
    add_job(uid, cid, "kept")
    src = FakeSite(SiteFetch([raw(1)], frozenset({"kept", "https://jobs.acme.test/jobs/1"})))
    await collect(uid, src)
    statuses = {j.external_id: j.status for j in job_rows(cid)}
    assert statuses["kept"] == "open"
    assert statuses["1"] == "open"
    assert get_company(cid).open_jobs == 2
    assert src.calls[0][0] == PAGE


async def test_listing_only_known_jobs_no_empty_guard(uid):
    cid = add_company(uid, "site", PAGE)
    for n in range(6):
        add_job(uid, cid, f"j{n}")
    listed = frozenset(f"j{n}" for n in range(6))
    src = FakeSite(SiteFetch([], listed))
    await collect(uid, src)
    assert get_company(cid).last_error is None
    assert all(j.status == "open" for j in job_rows(cid))


async def test_site_converted_to_embedded_ats(uid):
    cid = add_company(uid, "site", PAGE)
    src = FakeSite(EmbeddedAtsFound("lever", "acme"))
    await collect(uid, src)
    c = get_company(cid)
    assert (c.source, c.identifier) == ("lever", "acme")
    assert c.last_error == "Página de carreira usa Lever; a empresa passou a ser coletada direto pela API."
    assert c.next_run_at == NOW


async def test_site_conversion_when_ats_company_exists_pauses_site(uid):
    add_company(uid, "lever", "acme", next_run_at=NOW + timedelta(days=1))  # not due yet
    cid = add_company(uid, "site", PAGE)
    await collect(uid, FakeSite(EmbeddedAtsFound("lever", "acme")))
    c = get_company(cid)
    assert c.source == "site" and c.active is False
    assert "Lever" in c.last_error


async def test_blocked_site_pauses_company_with_message(uid):
    cid = add_company(uid, "site", PAGE)
    await collect(uid, FakeSite(SourceBlocked("jobs.acme.test")))
    c = get_company(cid)
    assert c.active is False
    assert c.last_error == cj.MSG_BLOCKED


async def test_no_structured_data_pauses_with_message(uid):
    cid = add_company(uid, "site", PAGE)
    await collect(uid, FakeSite(NoStructuredData(MSG_NO_STRUCTURED)))
    c = get_company(cid)
    assert c.active is False
    assert c.last_error == MSG_NO_STRUCTURED


async def test_robots_disallowed_records_failure_with_backoff(uid):
    cid = add_company(uid, "site", PAGE)
    await collect(uid, FakeSite(RobotsDisallowed("robots.txt não permite esta página")))
    c = get_company(cid)
    assert c.active is True
    assert c.consecutive_failures == 1
    assert c.last_error == cj.MSG_ROBOTS
    assert c.next_run_at == NOW + timedelta(minutes=15)


async def test_portal_site_not_collected_when_gate_closed(uid):
    cid = add_company(uid, "site", "https://www.catho.com.br/vagas/x/1/")
    src = FakeSite(SiteFetch([raw(1)], frozenset()))
    await collect(uid, src)
    assert src.calls == []
    assert get_company(cid).last_error == (
        "Portal desligado: os termos de uso não foram totalmente verificados. "
        "Ligue na aba Portais se aceitar o risco."
    )


async def test_blocked_portal_marks_blocked_by_site(uid, monkeypatch):
    # no portal has an allowed path yet (verification failed), so the gate is
    # opened here on purpose to reach the 403/429 branch for a portal host
    monkeypatch.setattr(cj, "_gate_message", lambda session_factory, c: None)
    with SessionLocal() as s:
        s.add(PortalState(user_id=uid, portal="vagas", enabled=True))
        s.commit()
    cid = add_company(uid, "site", "https://www.vagas.com.br/vagas/v1/x")
    await collect(uid, FakeSite(SourceBlocked("www.vagas.com.br")))
    assert get_company(cid).active is False
    with SessionLocal() as s:
        row = s.scalar(select(PortalState).where(PortalState.user_id == uid))
        assert row.blocked_by_site is True
        assert row.enabled is True


async def test_ats_company_gets_rank0_origin_ats(uid):
    class FakeAts:
        name = "greenhouse"

        async def fetch_jobs(self, identifier):
            return [raw(7)]

        async def aclose(self):
            pass

    cid = add_company(uid, "greenhouse", "acme")
    await collect(uid, FakeAts())
    job = job_rows(cid)[0]
    assert (job.origin, job.source_rank) == ("ats", 0)


async def test_site_jobs_origin_site_rank1(uid):
    cid = add_company(uid, "site", PAGE)
    await collect(uid, FakeSite(SiteFetch([raw(3)], frozenset())))
    job = job_rows(cid)[0]
    assert (job.origin, job.source_rank) == ("site", 1)
