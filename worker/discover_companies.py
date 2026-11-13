"""Worker job: automatic company discovery.

Turns discovery candidates (catalog, profile experiences, target companies)
into companies with origin='auto' when their board answers with open jobs.
Validation only uses the injected source connector. Logs carry candidate ids,
outcome kinds and exception class names only: never a token, a company name
(they come from the profile) or text from the source.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from rumo_core.discovery.candidates import refresh_candidates
from rumo_core.discovery.validate import validate_candidate
from rumo_core.models import Company, DiscoveryCandidate, User, UserPreference
from rumo_core.sources import get_source

logger = logging.getLogger("worker.discover_companies")

# Refresh the candidate list at most this often per user (monotonic seconds).
REFRESH_EVERY_SECONDS = 3600.0
# Claiming pushes retry_after this far ahead (committed before the fetch) so
# another worker cannot take the same candidate during a slow fetch; a worker
# that dies mid-fetch leaves it due again after the lease.
CLAIM_LEASE = timedelta(minutes=10)
EMPTY_RECHECK = timedelta(days=30)
TRANSIENT_BACKOFF_BASE = timedelta(hours=1)
MAX_NAME_LEN = 255

# user_id -> clock() of the last refresh_candidates call
_last_refresh: dict[int, float] = {}


def _reset_state() -> None:
    """Forget the per-user refresh times (used by tests)."""
    _last_refresh.clear()


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Claimed:
    id: int
    user_id: int
    source: str
    token: str
    name_hint: str | None


def _enabled_users(session_factory, user_id: int | None) -> list[int]:
    """Users with discovery enabled (no preference row = enabled)."""
    with session_factory() as s:
        stmt = (
            select(User.id)
            .outerjoin(UserPreference, UserPreference.user_id == User.id)
            .where(or_(UserPreference.user_id.is_(None),
                       UserPreference.discovery_enabled.is_(True)))
        )
        if user_id is not None:
            stmt = stmt.where(User.id == user_id)
        return list(s.scalars(stmt.order_by(User.id)))


def _maybe_refresh(session_factory, user_id: int, clock) -> None:
    t = clock()
    last = _last_refresh.get(user_id)
    if last is not None and t - last < REFRESH_EVERY_SECONDS:
        return
    _last_refresh[user_id] = t  # also on failure: no hot retry loop
    try:
        with session_factory() as s:
            refresh_candidates(s, user_id)
    except Exception as exc:  # noqa: BLE001 - one user's refresh must not stop the job
        logger.warning("user %s candidate refresh failed (%s)", user_id,
                       type(exc).__name__)


def _active_auto_count(s, user_id: int) -> int:
    return s.scalar(
        select(func.count()).select_from(Company).where(
            Company.user_id == user_id, Company.origin == "auto",
            Company.active.is_(True),
        )
    )


def _at_cap(session_factory, user_id: int, max_auto: int) -> bool:
    with session_factory() as s:
        return _active_auto_count(s, user_id) >= max_auto


def _existing_company_id(s, user_id: int, source: str, token: str) -> int | None:
    return s.scalar(
        select(Company.id).where(
            Company.user_id == user_id, Company.source == source,
            func.lower(Company.identifier) == token,
        ).order_by(Company.id).limit(1)
    )


def _claim(session_factory, user_id: int, now: datetime) -> _Claimed | None:
    """Claim the next due candidate (FOR UPDATE SKIP LOCKED) and commit a
    lease on it. Due: pending with retry_after NULL or past, or invalid with a
    past retry_after (empty board, rechecked after 30 days)."""
    cand = DiscoveryCandidate
    with session_factory() as s:
        row = s.execute(
            select(cand)
            .where(
                cand.user_id == user_id,
                or_(
                    and_(cand.state == "pending",
                         or_(cand.retry_after.is_(None), cand.retry_after <= now)),
                    and_(cand.state == "invalid", cand.retry_after.is_not(None),
                         cand.retry_after <= now),
                ),
            )
            .order_by(cand.retry_after.asc().nulls_first(), cand.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.state == "invalid":  # 30-day recheck starts a fresh cycle
            row.state = "pending"
            row.attempts = 0
            row.last_error = None
        row.retry_after = now + CLAIM_LEASE
        claimed = _Claimed(row.id, row.user_id, row.source, row.token, row.name_hint)
        s.commit()
        return claimed


def _mark_known_if_exists(session_factory, c: _Claimed, now: datetime) -> bool:
    """A company with this token already exists: candidate becomes known
    (no network needed). The company itself is never touched."""
    with session_factory() as s:
        cid = _existing_company_id(s, c.user_id, c.source, c.token)
        if cid is None:
            return False
        row = _lock_pending(s, c.id)
        if row is not None:
            row.state = "known"
            row.company_id = cid
            row.retry_after = None
            row.checked_at = now
        s.commit()
        return True


def _lock_pending(s, cand_id: int) -> DiscoveryCandidate | None:
    """The candidate, locked, if it is still pending (refresh or a user may
    have moved it meanwhile; then it is left alone)."""
    row = s.execute(
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.id == cand_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or row.state != "pending":
        return None
    return row


def _record_valid(session_factory, c: _Claimed, name: str | None, now: datetime,
                  max_auto: int) -> str:
    """Create the auto company; returns 'created', 'known', 'capped' or 'gone'.

    The user row is locked first so concurrent creations for the same user
    are serialised and the cap holds. Creation uses ON CONFLICT DO NOTHING on
    (user_id, source, identifier): a company that appeared meanwhile turns the
    candidate known and is never modified (a paused one stays paused).
    """
    with session_factory() as s:
        s.execute(select(User.id).where(User.id == c.user_id).with_for_update())
        row = _lock_pending(s, c.id)
        if row is None:
            s.rollback()
            return "gone"
        cid = _existing_company_id(s, c.user_id, c.source, c.token)
        if cid is None and _active_auto_count(s, c.user_id) >= max_auto:
            row.retry_after = None  # retried once a slot frees up
            s.commit()
            return "capped"
        if cid is None:
            company_name = (name or c.name_hint or c.token)[:MAX_NAME_LEN]
            cid = s.execute(
                insert(Company)
                .values(user_id=c.user_id, source=c.source, identifier=c.token,
                        name=company_name, origin="auto", active=True,
                        next_run_at=now)
                .on_conflict_do_nothing(
                    constraint="uq_companies_user_source_identifier"
                )
                .returning(Company.id)
            ).scalar_one_or_none()
            if cid is not None:
                row.state = "valid"
                row.company_id = cid
                row.retry_after = None
                row.last_error = None
                row.checked_at = now
                s.commit()
                return "created"
            cid = _existing_company_id(s, c.user_id, c.source, c.token)
        row.state = "known"
        row.company_id = cid
        row.retry_after = None
        row.checked_at = now
        s.commit()
        return "known"


def _record_invalid(session_factory, c: _Claimed, error: str | None,
                    retry_after: datetime | None, now: datetime) -> None:
    with session_factory() as s:
        row = _lock_pending(s, c.id)
        if row is None:
            return
        row.state = "invalid"
        row.last_error = error
        row.retry_after = retry_after
        row.checked_at = now
        s.commit()


def _transient_backoff(attempts: int) -> timedelta:
    return TRANSIENT_BACKOFF_BASE * 2 ** min(max(attempts - 1, 0), 10)


def _record_transient(session_factory, c: _Claimed, error: str, now: datetime,
                      max_attempts: int) -> None:
    with session_factory() as s:
        row = _lock_pending(s, c.id)
        if row is None:
            return
        row.attempts += 1
        row.last_error = error
        row.checked_at = now
        if row.attempts >= max_attempts:
            row.state = "invalid"
            row.retry_after = None
        else:
            row.retry_after = now + _transient_backoff(row.attempts)
        s.commit()


async def _validate_one(session_factory, c: _Claimed, *, source_getter, utcnow,
                        max_auto: int, max_attempts: int) -> str:
    """Fetch + record one claimed candidate; returns the recorded result."""
    try:
        source = source_getter(c.source)
    except KeyError:
        logger.warning("candidate %s: unknown source", c.id)
        _record_invalid(session_factory, c, "UnknownSource", None, utcnow())
        return "invalid"
    try:
        outcome = await validate_candidate(source, c.token)  # no DB transaction held
    except Exception as exc:  # noqa: BLE001 - unexpected: retry later
        logger.warning("candidate %s unexpected validation failure (%s)", c.id,
                       type(exc).__name__)
        _record_transient(session_factory, c, type(exc).__name__, utcnow(), max_attempts)
        return "transient"
    now = utcnow()
    if outcome.kind == "valid":
        result = _record_valid(session_factory, c, outcome.name, now, max_auto)
    elif outcome.kind == "invalid":
        retry = None if outcome.error else now + EMPTY_RECHECK
        _record_invalid(session_factory, c, outcome.error, retry, now)
        result = "invalid"
    else:
        _record_transient(session_factory, c, outcome.error or "Transient", now,
                          max_attempts)
        result = "transient"
    logger.info("candidate %s: %s%s", c.id, result,
                f" ({outcome.error})" if outcome.error else "")
    return result


async def discover_companies(
    session_factory,
    *,
    batch: int,
    max_seconds: float,
    max_auto: int,
    delay: float,
    max_attempts: int,
    user_id: int | None = None,
    source_getter=get_source,
    clock=time.monotonic,
    sleep=asyncio.sleep,
    utcnow=_utcnow,
) -> int:
    """Validate pending discovery candidates; returns how many were processed.

    Per user with discovery enabled (no preference row = enabled): refreshes
    the candidates at most once an hour, then claims due candidates one at a
    time (FOR UPDATE SKIP LOCKED + committed lease), fetches the board with no
    DB transaction open and records the outcome. Stops after `batch`
    candidates, after `max_seconds` (measured with `clock`), and per user once
    `max_auto` active automatic companies exist. Waits `delay` seconds between
    network calls. A candidate whose company already exists becomes known
    without a fetch.
    """
    processed = 0
    fetched = 0
    started = clock()

    def out_of_budget() -> bool:
        return processed >= batch or clock() - started >= max_seconds

    for uid in _enabled_users(session_factory, user_id):
        if out_of_budget():
            break
        _maybe_refresh(session_factory, uid, clock)
        while not out_of_budget():
            if _at_cap(session_factory, uid, max_auto):
                break
            claimed = _claim(session_factory, uid, utcnow())
            if claimed is None:
                break
            processed += 1
            if _mark_known_if_exists(session_factory, claimed, utcnow()):
                continue
            if fetched:
                await sleep(delay)
            fetched += 1
            result = await _validate_one(
                session_factory, claimed, source_getter=source_getter, utcnow=utcnow,
                max_auto=max_auto, max_attempts=max_attempts,
            )
            if result == "capped":
                break
    return processed
