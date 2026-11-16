"""Feed jobs: synthetic employer companies, ingest behind the funnel, discovery seeds.

A feed item hangs off a synthetic company per employer (`source=<feed>`,
`identifier=employer_key(name)`, inactive, `origin="feed"`, rank 2). Items whose
employer has no usable name go to `_desconhecida`, which never dedupes against
any other company (and not against itself either: see `ingest.UNKNOWN_EMPLOYER`).
Employers that can be turned into a board token also seed discovery candidates
for every ATS and, when the feed gives a website, for the career site.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rumo_core.discovery import DISCOVERY_SOURCES
from rumo_core.discovery.names import employer_key, token_variants
from rumo_core.matching.filters import SearchFilters
from rumo_core.models import Company, DiscoveryCandidate, Job, UserPreference
from rumo_core.sources.feeds.base import FeedItem
from rumo_core.sources.http import UnsafeURL, canonical_url, registrable_domain
from rumo_core.sources.ingest import UNKNOWN_EMPLOYER, IngestPolicy, ingest_jobs

UNKNOWN_NAME = "Empresa não informada"
MAX_TOKEN = 64


@dataclass
class FeedIngestResult:
    companies: int = 0
    created: int = 0
    stubs: int = 0
    candidates: int = 0


def _identifier_for(employer_name: str | None) -> str:
    return employer_key(employer_name) or UNKNOWN_EMPLOYER


def synthetic_company(
    session: Session, user_id: int, feed: str, employer_name: str | None,
) -> Company:
    """The feed's company for this employer (created on first sight). Does not commit."""
    identifier = _identifier_for(employer_name)
    company = session.scalar(
        select(Company).where(
            Company.user_id == user_id, Company.source == feed, Company.identifier == identifier,
        )
    )
    if company is None:
        name = (employer_name or "").strip()[:255] or UNKNOWN_NAME
        company = Company(
            user_id=user_id, source=feed, identifier=identifier, name=name,
            origin="feed", active=False,
        )
        session.add(company)
        session.flush()
    return company


def _core_token(name: str | None) -> str | None:
    """The token used for ATS discovery: the name without legal suffixes, joined."""
    if not isinstance(name, str) or employer_key(name) is None:
        return None
    variants = token_variants(name)
    if not variants:
        return None
    return variants[1] if len(variants) > 1 else variants[0]


def _site_domain(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        host = urlsplit(canonical_url(url)).hostname or ""
    except UnsafeURL:
        return None
    domain = registrable_domain(host)[:MAX_TOKEN] if host else ""
    return domain or None


def _discovery_enabled(session: Session, user_id: int) -> bool:
    enabled = session.scalar(
        select(UserPreference.discovery_enabled).where(UserPreference.user_id == user_id)
    )
    return enabled is None or enabled is True  # no preference row = enabled (as the worker)


def seed_feed_candidates(
    session: Session, user_id: int, employers: Iterable[tuple[str | None, str | None]],
) -> int:
    """Discovery candidates for feed employers (one per ATS, plus the career site).

    Only with discovery on. Idempotent (existing candidates are kept as they are).
    Returns how many were inserted. Does not commit.
    """
    if not _discovery_enabled(session, user_id):
        return 0
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, token: str, hint: str | None) -> None:
        if (source, token) in seen:
            return
        seen.add((source, token))
        rows.append({
            "user_id": user_id, "source": source, "token": token,
            "name_hint": hint[:255] if hint else None, "origin_kind": "feed",
            "state": "pending",
        })

    for name, url in employers:
        token = _core_token(name)
        if token is not None:
            for source in DISCOVERY_SOURCES:
                add(source, token, name)
        domain = _site_domain(url)
        if domain is not None:
            add("site", domain, name)
    if not rows:
        return 0
    stmt = (
        insert(DiscoveryCandidate)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_discovery_candidates_user_source_token")
        .returning(DiscoveryCandidate.id)
    )
    return len(session.execute(stmt).scalars().all())


def ingest_feed_items(
    session: Session, user_id: int, feed: str, items: list[FeedItem], *, now: datetime,
    filters: SearchFilters | None, keywords: tuple[str, ...],
) -> FeedIngestResult:
    """Store the feed's items under their synthetic companies, through the funnel.

    Feed absence never closes jobs (`close_missing=False`); stale ones close in
    `close_stale_feed_jobs`. Also seeds discovery candidates. Does not commit.
    """
    groups: dict[str, list[FeedItem]] = {}
    for item in items:
        groups.setdefault(_identifier_for(item.employer_name), []).append(item)
    res = FeedIngestResult()
    policy = IngestPolicy(
        origin="feed", source_rank=2, close_missing=False, filters=filters, keywords=keywords,
    )
    for group in groups.values():
        company = synthetic_company(session, user_id, feed, group[0].employer_name)
        result = ingest_jobs(
            session, user_id, company, [item.raw for item in group], now=now, policy=policy,
        )
        res.companies += 1
        res.created += result.created
        res.stubs += result.stubs
    res.candidates = seed_feed_candidates(
        session, user_id, [(item.employer_name, item.employer_url) for item in items],
    )
    return res


def close_stale_feed_jobs(
    session: Session, user_id: int, feed: str, *, now: datetime, ttl: timedelta,
) -> int:
    """Close the feed's open/duplicate jobs not seen for longer than `ttl`. Returns how many."""
    cutoff = now - ttl
    jobs = session.scalars(
        select(Job)
        .join(Company, Company.id == Job.company_id)
        .where(
            Job.user_id == user_id, Job.status.in_(("open", "duplicate")),
            Job.last_seen_at < cutoff, Company.origin == "feed", Company.source == feed,
        )
    ).all()
    touched: set[int] = set()
    for job in jobs:
        job.status = "closed"
        job.closed_at = now
        touched.add(job.company_id)
    for company_id in touched:
        company = session.get(Company, company_id)
        company.open_jobs = session.scalar(
            select(func.count()).select_from(Job).where(
                Job.company_id == company_id, Job.status == "open", Job.intake != "stub",
            )
        )
    session.flush()
    return len(jobs)
