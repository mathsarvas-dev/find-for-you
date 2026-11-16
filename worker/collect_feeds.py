"""Worker job: collect the users' aggregator feeds (`job_feeds`) behind the compliance gate.

Same shape as `collect_companies`: claim the most overdue feed with a committed
lease, fetch without a DB transaction, then ingest + health update in one
transaction. Before any request the gate is checked (policy verdict + the
user's choice for conditional feeds) and the API key, when needed.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from rumo_core.models import JobFeed
from rumo_core.sources.base import SourceBadResponse, SourceBlocked, SourceNotFound, SourceUnavailable
from rumo_core.sources.feed_ingest import close_stale_feed_jobs, ingest_feed_items
from rumo_core.sources.feeds import feed_needs_key, get_feed
from rumo_core.sources.feeds.base import FeedParams
from rumo_core.sources.funnel import funnel_filters
from rumo_core.sources.portals.gate import check, load_policy
from rumo_core.sources.portals.state import load_states, mark_blocked_by_site
from worker.collect_jobs import (
    CLAIM_LEASE,
    MSG_BAD_RESPONSE,
    MSG_BLOCKED,
    MSG_NOT_FOUND,
    MSG_UNAVAILABLE,
    MSG_UNEXPECTED,
    MSG_UNKNOWN_SOURCE,
    _backoff,
)

logger = logging.getLogger("worker.collect_feeds")

MSG_NO_KEY = "Chave da API não configurada no .env."
MSG_BAD_PARAMS = "Parâmetros do feed inválidos."


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedFeed:
    feed_id: int
    user_id: int
    source: str
    params: dict


def _claim_feed(session_factory, user_id: int | None, now: datetime) -> ClaimedFeed | None:
    with session_factory() as s:
        stmt = select(JobFeed).where(
            JobFeed.active.is_(True),
            or_(JobFeed.next_run_at.is_(None), JobFeed.next_run_at <= now),
        )
        if user_id is not None:
            stmt = stmt.where(JobFeed.user_id == user_id)
        row = s.execute(
            stmt.order_by(JobFeed.next_run_at.asc().nulls_first(), JobFeed.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.next_run_at = now + CLAIM_LEASE
        claimed = ClaimedFeed(row.id, row.user_id, row.source, dict(row.params or {}))
        s.commit()
        return claimed


def _interval(source: str, interval_hours: int) -> timedelta:
    """The configured interval, but never shorter than the feed's policy interval."""
    entry = load_policy().get(source)
    minimum = timedelta(seconds=entry.min_interval_seconds) if entry else timedelta(0)
    return max(timedelta(hours=interval_hours), minimum)


def _gate_message(session_factory, c: ClaimedFeed) -> str | None:
    with session_factory() as s:
        state = load_states(s, c.user_id).get(c.source)
    verdict = check(c.source, state=state)
    return None if verdict.allowed else verdict.message


def _record_skip(session_factory, feed_id: int, msg: str, now, interval_hours: int,
                 source: str) -> None:
    """Not collected this time (gate or key): no failure count, same message for the user."""
    with session_factory() as s:
        row = s.execute(select(JobFeed).where(JobFeed.id == feed_id).with_for_update()).scalar_one_or_none()
        if row is None:
            return
        row.last_run_at = now
        row.last_error = msg
        row.next_run_at = now + _interval(source, interval_hours)
        s.commit()


def _record_failure(session_factory, feed_id: int, msg: str, now, interval_hours: int,
                    *, blocked: bool = False) -> None:
    with session_factory() as s:
        row = s.execute(select(JobFeed).where(JobFeed.id == feed_id).with_for_update()).scalar_one_or_none()
        if row is None:
            return
        row.consecutive_failures += 1
        row.last_run_at = now
        row.last_error = msg
        row.next_run_at = now + _backoff(row.consecutive_failures, interval_hours)
        if blocked:
            row.active = False
            mark_blocked_by_site(s, row.user_id, row.source, now)
        s.commit()


def _record_success(session_factory, c: ClaimedFeed, items, now, interval_hours: int,
                    ttl: timedelta) -> None:
    """Ingest + close stale jobs + health update in ONE transaction."""
    with session_factory() as s:
        row = s.execute(select(JobFeed).where(JobFeed.id == c.feed_id).with_for_update()).scalar_one_or_none()
        if row is None:
            return
        params = FeedParams.from_dict(c.params)
        ingest_feed_items(
            s, c.user_id, c.source, items, now=now,
            filters=funnel_filters(s, c.user_id), keywords=params.keywords,
        )
        close_stale_feed_jobs(s, c.user_id, c.source, now=now, ttl=ttl)
        row.last_run_at = now
        row.last_error = None
        row.consecutive_failures = 0
        row.jobs_seen = len(items)
        row.next_run_at = now + _interval(c.source, interval_hours)
        s.commit()


async def _collect_one_feed(
    session_factory, c: ClaimedFeed, *, feed_getter, clock, interval_hours: int,
    ttl: timedelta, settings,
) -> None:
    now = clock()
    try:
        try:
            feed = feed_getter(c.source)
        except KeyError:
            logger.warning("feed %s: unknown", c.feed_id)
            _record_failure(session_factory, c.feed_id, MSG_UNKNOWN_SOURCE, now, interval_hours)
            return
        msg = _gate_message(session_factory, c)
        if msg is not None:
            _record_skip(session_factory, c.feed_id, msg, now, interval_hours, c.source)
            return
        if feed_needs_key(c.source) and not feed.key_configured(settings):
            _record_skip(session_factory, c.feed_id, MSG_NO_KEY, now, interval_hours, c.source)
            return
        try:
            params = FeedParams.from_dict(c.params)
        except ValueError:
            _record_failure(session_factory, c.feed_id, MSG_BAD_PARAMS, now, interval_hours)
            return
        items = await feed.fetch(params)  # no DB transaction held
        await asyncio.to_thread(_record_success, session_factory, c, items, clock(),
                                interval_hours, ttl)
    except SourceBlocked:
        logger.warning("feed %s: source refused access repeatedly", c.feed_id)
        _record_failure(session_factory, c.feed_id, MSG_BLOCKED, clock(), interval_hours,
                        blocked=True)
    except SourceNotFound:
        logger.warning("feed %s: not found", c.feed_id)
        _record_failure(session_factory, c.feed_id, MSG_NOT_FOUND, clock(), interval_hours)
    except SourceUnavailable:
        logger.warning("feed %s: source unavailable", c.feed_id)
        _record_failure(session_factory, c.feed_id, MSG_UNAVAILABLE, clock(), interval_hours)
    except SourceBadResponse:
        logger.warning("feed %s: unexpected response", c.feed_id)
        _record_failure(session_factory, c.feed_id, MSG_BAD_RESPONSE, clock(), interval_hours)
    except Exception:  # noqa: BLE001 - one feed never stops the others
        logger.exception("feed %s unexpected collect failure", c.feed_id)
        _record_failure(session_factory, c.feed_id, MSG_UNEXPECTED, clock(), interval_hours)


async def collect_feeds(
    session_factory,
    *,
    feed_getter=get_feed,
    user_id: int | None = None,
    clock=_utcnow,
    sleep=asyncio.sleep,
    interval_hours: int,
    delay_seconds: float,
    ttl_days: int,
    max_feeds: int | None = None,
    max_seconds: float | None = None,
    settings=None,
) -> int:
    """Collect due feeds one at a time; returns how many were processed.

    Stops claiming after `max_feeds` or `max_seconds` (measured with `clock`);
    a claimed feed is always finished. Waits `delay_seconds` between feeds.
    """
    if settings is None:
        from rumo_core.config import get_settings

        settings = get_settings()
    ttl = timedelta(days=ttl_days)
    processed = 0
    started = clock()
    while True:
        if max_feeds is not None and processed >= max_feeds:
            return processed
        now = clock()
        if max_seconds is not None and (now - started).total_seconds() >= max_seconds:
            return processed
        claimed = _claim_feed(session_factory, user_id, now)
        if claimed is None:
            return processed
        if processed:
            await sleep(delay_seconds)
        await _collect_one_feed(
            session_factory, claimed, feed_getter=feed_getter, clock=clock,
            interval_hours=interval_hours, ttl=ttl, settings=settings,
        )
        processed += 1
