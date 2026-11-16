import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select, update

from rumo_core.db import SessionLocal
from rumo_core.models import Company, DiscoveryCandidate, Job, JobFeed, PortalState, User
from rumo_core.sources.base import RawJob, SourceBlocked, SourceUnavailable
from rumo_core.sources.feeds.base import FeedItem
from rumo_core.sources.feeds.remotive import RemotiveFeed
from rumo_core.sources.http import SafeClient
from rumo_core.sources.portals.gate import MSG_CONDITIONAL_OFF
from worker import collect_feeds as cf
from worker.collect_feeds import collect_feeds
from worker.collect_jobs import MSG_BLOCKED

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
FIX = Path(__file__).resolve().parent / "fixtures" / "remotive" / "remote-jobs.json"
PUBLIC = "93.184.216.34"


def item(eid: str, employer: str = "Acme Ltda") -> FeedItem:
    raw = RawJob(
        external_id=eid, title=f"Vaga {eid}", url=f"https://remotive.com/jobs/{eid}",
        location_text="Remote", description_html="<p>Python.</p>", department=None,
        posted_at=None, raw={"id": eid},
    )
    return FeedItem(raw=raw, employer_name=employer)


class FakeFeed:
    def __init__(self, name, items=None, error=None, key=True):
        self.name = name
        self.items = items or []
        self.error = error
        self.key = key
        self.calls: list = []

    async def fetch(self, params, *, known_ids=frozenset()):
        self.calls.append(params)
        if self.error is not None:
            raise self.error
        return list(self.items)

    def key_configured(self, settings):
        return self.key

    async def aclose(self):
        pass


class Sleeps:
    async def __call__(self, s):
        return None


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"worker-feeds-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(PortalState).where(PortalState.user_id == user_id))
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(JobFeed).where(JobFeed.user_id == user_id))
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def add_feed(uid, source, params=None):
    with SessionLocal() as s:
        row = JobFeed(user_id=uid, source=source, params=params or {}, active=True)
        s.add(row)
        s.commit()
        return row.id


def feed_row(fid):
    with SessionLocal() as s:
        return s.get(JobFeed, fid)


async def collect(uid, feeds, *, now=NOW, interval_hours=1, **kw):
    return await collect_feeds(
        SessionLocal, feed_getter=lambda name: feeds[name], user_id=uid,
        clock=lambda: now, sleep=Sleeps(), interval_hours=interval_hours,
        delay_seconds=0, ttl_days=7, settings=_settings(), **kw,
    )


def _settings():
    from rumo_core.config import Settings

    return Settings(_env_file=None, database_url="postgresql+psycopg://x")


async def test_due_feed_collected_and_rescheduled(uid):
    rem = add_feed(uid, "remotive")
    rok = add_feed(uid, "remoteok")
    feeds = {
        "remotive": FakeFeed("remotive", [item("1")]),
        "remoteok": FakeFeed("remoteok", [item("2", "Beta SA")]),
    }
    assert await collect(uid, feeds) == 2
    assert feed_row(rem).next_run_at == NOW + timedelta(hours=6)  # policy: 21600 s
    assert feed_row(rok).next_run_at == NOW + timedelta(hours=1)  # policy 3600 s = config 1 h
    assert feed_row(rem).jobs_seen == 1 and feed_row(rem).last_error is None
    with SessionLocal() as s:
        assert len(s.scalars(select(Job).where(Job.user_id == uid)).all()) == 2


async def test_conditional_feed_skipped_until_enabled(uid):
    fid = add_feed(uid, "bundesagentur")
    fake = FakeFeed("bundesagentur", [item("1")])
    await collect(uid, {"bundesagentur": fake})
    assert fake.calls == []
    assert feed_row(fid).last_error == MSG_CONDITIONAL_OFF
    with SessionLocal() as s:
        s.add(PortalState(user_id=uid, portal="bundesagentur", enabled=True))
        s.execute(update(JobFeed).where(JobFeed.id == fid).values(next_run_at=None))
        s.commit()
    await collect(uid, {"bundesagentur": fake})
    assert len(fake.calls) == 1


async def test_keyed_feed_without_key_skipped(uid, monkeypatch):
    monkeypatch.setattr(cf, "feed_needs_key", lambda name: True)
    fid = add_feed(uid, "remotive")
    fake = FakeFeed("remotive", [item("1")], key=False)
    await collect(uid, {"remotive": fake})
    assert fake.calls == []
    assert feed_row(fid).last_error == cf.MSG_NO_KEY


async def test_blocked_feed_paused_and_portal_marked(uid):
    fid = add_feed(uid, "remotive")
    await collect(uid, {"remotive": FakeFeed("remotive", error=SourceBlocked("remotive.com"))})
    row = feed_row(fid)
    assert row.active is False
    assert row.last_error == MSG_BLOCKED
    with SessionLocal() as s:
        state = s.scalar(select(PortalState).where(PortalState.user_id == uid,
                                                   PortalState.portal == "remotive"))
        assert state is not None and state.blocked_by_site is True


async def test_feed_failure_backoff(uid):
    fid = add_feed(uid, "remotive")
    await collect(uid, {"remotive": FakeFeed("remotive", error=SourceUnavailable("x"))})
    row = feed_row(fid)
    assert row.consecutive_failures == 1
    assert row.next_run_at == NOW + timedelta(minutes=15)
    assert row.last_error == "Fonte indisponível no momento."


async def test_two_feeds_same_host_respect_min_interval(uid):
    """Review focus 3: two remotive feeds due in one tick send one request to the host."""
    body = FIX.read_bytes()
    sent: list[str] = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async def resolve(host, port):
        return [PUBLIC]

    async def no_sleep(seconds):
        return None

    client = SafeClient(httpx.MockTransport(handler), resolve=resolve, sleep=no_sleep,
                        clock=lambda: 0.0)
    feed = RemotiveFeed(client, max_bytes=20_000_000)
    first = add_feed(uid, "remotive", {"keywords": ["a"]})
    second = add_feed(uid, "remotive", {"keywords": ["b"]})
    await collect(uid, {"remotive": feed}, max_feeds=2)
    assert len(sent) == 1
    failed = [r for r in (feed_row(first), feed_row(second)) if r.last_error]
    assert len(failed) == 1
    assert failed[0].consecutive_failures == 1


def test_collect_feeds_registration_uses_settings():
    from functools import partial

    from worker.main import build_collect_feeds_job

    s = _settings()
    job = build_collect_feeds_job(s, SessionLocal)
    assert isinstance(job, partial)
    assert job.func is collect_feeds
    assert job.keywords["ttl_days"] == s.feed_job_ttl_days
    assert job.keywords["max_feeds"] == s.collect_feeds_max_per_run
