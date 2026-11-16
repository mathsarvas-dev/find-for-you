import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.discovery import DISCOVERY_SOURCES
from rumo_core.matching.filters import SearchFilters
from rumo_core.models import Company, DiscoveryCandidate, Job, User, UserPreference
from rumo_core.sources.base import RawJob
from rumo_core.sources.feed_ingest import (
    UNKNOWN_NAME,
    close_stale_feed_jobs,
    ingest_feed_items,
    seed_feed_candidates,
)
from rumo_core.sources.feeds.base import FeedItem
from rumo_core.sources.ingest import UNKNOWN_EMPLOYER, IngestPolicy, ingest_jobs

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def item(eid, title, *, employer="Acme Ltda", url=None, location="Remote, Brazil",
         description="<p>Python backend.</p>"):
    raw = RawJob(
        external_id=eid, title=title, url=f"https://remotive.com/jobs/{eid}",
        location_text=location, description_html=description, department=None,
        posted_at=None, raw={"id": eid}, work_model_hint="remote",
    )
    return FeedItem(raw=raw, employer_name=employer, employer_url=url)


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"feed-ingest-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == user_id))
        s.execute(delete(UserPreference).where(UserPreference.user_id == user_id))
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def ingest(uid, feed, items, *, now=NOW, filters=None, keywords=()):
    with SessionLocal() as s:
        res = ingest_feed_items(s, uid, feed, items, now=now, filters=filters, keywords=keywords)
        s.commit()
        return res


def companies(uid):
    with SessionLocal() as s:
        return list(s.scalars(select(Company).where(Company.user_id == uid).order_by(Company.id)))


def jobs(uid):
    with SessionLocal() as s:
        return list(s.scalars(select(Job).where(Job.user_id == uid).order_by(Job.id)))


def test_items_grouped_into_synthetic_companies(uid):
    res = ingest(uid, "remotive", [
        item("1", "Dev A", employer="Acme Ltda"),
        item("2", "Dev B", employer="ACME"),
        item("3", "Dev C", employer="Beta SA"),
    ])
    assert res.companies == 2
    assert res.created == 3
    rows = companies(uid)
    assert sorted(c.identifier for c in rows) == ["acme", "beta"]
    assert all(c.origin == "feed" and c.active is False and c.source == "remotive" for c in rows)


def test_unknown_employer_never_cross_deduped(uid):
    # an ATS company of the same title/location must keep its job open
    with SessionLocal() as s:
        ats = Company(user_id=uid, source="greenhouse", identifier="acme", name="Acme Ltda",
                      active=True, origin="auto")
        s.add(ats)
        s.flush()
        ingest_jobs(s, uid, ats, [item("ats-1", "Dev Python", employer="Acme Ltda").raw],
                    now=NOW, policy=IngestPolicy(origin="ats", source_rank=0))
        s.commit()
    ingest(uid, "remotive", [
        item("9", "Dev Python", employer=None),
        item("10", "Dev Python", employer="Ltda"),
    ])
    rows = {j.external_id: j for j in jobs(uid)}
    assert rows["ats-1"].status == "open"
    assert rows["9"].status == "open"
    assert rows["10"].status == "open"  # same company, different unknown employers: not merged
    unknown = [c for c in companies(uid) if c.identifier == UNKNOWN_EMPLOYER]
    assert len(unknown) == 1 and unknown[0].name == UNKNOWN_NAME


def test_unknown_name_placeholder_when_no_employer(uid):
    ingest(uid, "remotive", [item("1", "Dev", employer=None)])
    rows = [c for c in companies(uid) if c.identifier == UNKNOWN_EMPLOYER]
    assert rows[0].name == UNKNOWN_NAME


def test_feed_jobs_rank2_origin_feed(uid):
    ingest(uid, "remoteok", [item("1", "Dev")])
    job = jobs(uid)[0]
    assert (job.origin, job.source_rank, job.intake) == ("feed", 2, "full")


def test_feed_absence_never_closes(uid):
    ingest(uid, "remotive", [item("1", "A"), item("2", "B")])
    ingest(uid, "remotive", [item("1", "A")], now=NOW + timedelta(days=1))
    statuses = {j.external_id: j.status for j in jobs(uid)}
    assert statuses == {"1": "open", "2": "open"}


def test_close_stale_feed_jobs_after_ttl(uid):
    ingest(uid, "remotive", [item("old", "A"), item("fresh", "B")])
    with SessionLocal() as s:
        for job in s.scalars(select(Job).where(Job.user_id == uid)):
            job.last_seen_at = NOW - timedelta(days=8) if job.external_id == "old" \
                else NOW - timedelta(days=6)
        s.commit()
    with SessionLocal() as s:
        closed = close_stale_feed_jobs(s, uid, "remotive", now=NOW, ttl=timedelta(days=7))
        s.commit()
    assert closed == 1
    statuses = {j.external_id: j.status for j in jobs(uid)}
    assert statuses == {"old": "closed", "fresh": "open"}


def test_seed_feed_candidates_per_ats_and_site(uid):
    with SessionLocal() as s:
        n = seed_feed_candidates(s, uid, [("Acme Ltda", "https://www.acme.com.br/")])
        s.commit()
    assert n == len(DISCOVERY_SOURCES) + 1
    with SessionLocal() as s:
        rows = list(s.scalars(select(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid)))
    tokens = {(r.source, r.token) for r in rows}
    assert ("greenhouse", "acme") in tokens
    assert ("site", "acme.com.br") in tokens
    assert all(r.origin_kind == "feed" and r.state == "pending" for r in rows)


def test_seed_is_idempotent(uid):
    with SessionLocal() as s:
        first = seed_feed_candidates(s, uid, [("Acme Ltda", None)])
        s.commit()
    with SessionLocal() as s:
        second = seed_feed_candidates(s, uid, [("Acme Ltda", None)])
        s.commit()
    assert first == len(DISCOVERY_SOURCES) and second == 0


def test_seed_skips_when_discovery_disabled(uid):
    with SessionLocal() as s:
        s.add(UserPreference(user_id=uid, discovery_enabled=False))
        s.commit()
    with SessionLocal() as s:
        assert seed_feed_candidates(s, uid, [("Acme Ltda", "https://www.acme.com.br/")]) == 0
        s.commit()


def test_seed_skips_unknown_employer(uid):
    with SessionLocal() as s:
        assert seed_feed_candidates(s, uid, [(None, None), ("Ltda", None)]) == 0


def test_keywords_send_unmatched_items_to_stub(uid):
    res = ingest(uid, "remotive", [item("1", "Designer", description="<p>Figma.</p>"),
                                   item("2", "Python dev", description="<p>Python.</p>")],
                 filters=SearchFilters(), keywords=("python",))
    assert res.stubs == 1
    stubs = {j.external_id: j.intake for j in jobs(uid)}
    assert stubs == {"1": "stub", "2": "full"}
