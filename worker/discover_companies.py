"""Worker job: automatic company discovery.

Turns discovery candidates (catalog, profile experiences, target companies)
into companies with origin='auto' when their board answers with open jobs.
Validation only uses the injected source connector. Logs carry candidate ids,
outcome kinds and exception class names only: never a token, a company name
(they come from the profile) or text from the source.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
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

# The person's own choices first: explicit targets, then past employers, then
# the generic catalog (so the catalog can never use up the cap before them).
_ORIGIN_PRIORITY = case(
    (DiscoveryCandidate.origin_kind == "target", 0),
    (DiscoveryCandidate.origin_kind == "profile", 1),
    else_=2,
)

# user_id -> clock() of the last refresh_candidates call
_last_refresh: dict[int, float] = {}
# id of the user served first on the previous tick (round-robin start)
_cursor: int | None = None


def _reset_state() -> None:
    """Forget the per-user refresh times and the round-robin cursor (tests)."""
    global _cursor
    _last_refresh.clear()
    _cursor = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Claimed:
    id: int
    user_id: int
    source: str
    token: str
    name_hint: str | None
    # 30-day recheck of a board that was empty: the candidate stays 'invalid'
    # during the cycle and transient failures never make it permanent
    recheck: bool = False

    @property
    def state(self) -> str:
        return "invalid" if self.recheck else "pending"


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


def _rotate(users: list[int]) -> list[int]:
    """Start after the user served first last tick (wrapping around)."""
    if _cursor is None:
        return users
    i = next((k for k, u in enumerate(users) if u > _cursor), 0)
    return users[i:] + users[:i]


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
    past retry_after (empty board, rechecked after 30 days). Order: origin
    (target > profile > catalog), then retry_after NULLS FIRST, then id."""
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
            .order_by(_ORIGIN_PRIORITY, cand.retry_after.asc().nulls_first(), cand.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.retry_after = now + CLAIM_LEASE
        claimed = _Claimed(row.id, row.user_id, row.source, row.token, row.name_hint,
                           recheck=row.state == "invalid")
        s.commit()
        return claimed


def _lock_claimed(s, c: _Claimed) -> DiscoveryCandidate | None:
    """The candidate, locked, if it is still in the state it was claimed in
    (refresh or a user may have moved it meanwhile; then it is left alone)."""
    row = s.execute(
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.id == c.id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or row.state != c.state:
        return None
    return row


def _mark_known_if_exists(session_factory, c: _Claimed, now: datetime) -> bool:
    """A company with this token already exists: candidate becomes known
    (no network needed). The company itself is never touched."""
    with session_factory() as s:
        cid = _existing_company_id(s, c.user_id, c.source, c.token)
        if cid is None:
            return False
        row = _lock_claimed(s, c)
        if row is not None:
            _set_known(row, cid, now)
        s.commit()
        return True


def _set_known(row: DiscoveryCandidate, cid: int | None, now: datetime) -> None:
    row.state = "known"
    row.company_id = cid
    row.retry_after = None
    row.attempts = 0
    row.checked_at = now


def _record_valid(session_factory, c: _Claimed, name: str | None, now: datetime,
                  max_auto: int) -> str:
    """Create the auto company; returns 'created', 'known', 'capped' or 'gone'.

    The user row is locked first (FOR NO KEY UPDATE: serialises discover
    workers for that user without blocking FK inserts) so the cap holds.
    Creation uses ON CONFLICT DO NOTHING on (user_id, source, identifier): a
    company that appeared meanwhile turns the candidate known and is never
    modified (a paused one stays paused).
    """
    with session_factory() as s:
        s.execute(
            select(User.id).where(User.id == c.user_id).with_for_update(key_share=True)
        )
        row = _lock_claimed(s, c)
        if row is None:
            s.rollback()
            return "gone"
        cid = _existing_company_id(s, c.user_id, c.source, c.token)
        if cid is None and _active_auto_count(s, c.user_id) >= max_auto:
            # retried once a slot frees up (a recheck keeps its own schedule)
            row.retry_after = now + EMPTY_RECHECK if c.recheck else None
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
                row.attempts = 0
                row.last_error = None
                row.checked_at = now
                s.commit()
                return "created"
            cid = _existing_company_id(s, c.user_id, c.source, c.token)
        _set_known(row, cid, now)
        s.commit()
        return "known"


def _record_invalid(session_factory, c: _Claimed, error: str | None,
                    retry_after: datetime | None, now: datetime) -> None:
    with session_factory() as s:
        row = _lock_claimed(s, c)
        if row is None:
            return
        row.state = "invalid"
        row.last_error = error
        row.retry_after = retry_after
        if retry_after is not None:  # empty board: a recheck starts at 0 attempts
            row.attempts = 0
        row.checked_at = now
        s.commit()


def _transient_backoff(attempts: int) -> timedelta:
    return TRANSIENT_BACKOFF_BASE * 2 ** min(max(attempts - 1, 0), 10)


def _record_transient(session_factory, c: _Claimed, error: str, now: datetime,
                      max_attempts: int) -> None:
    with session_factory() as s:
        row = _lock_claimed(s, c)
        if row is None:
            return
        row.attempts += 1
        row.last_error = error
        row.checked_at = now
        if row.attempts < max_attempts:
            row.retry_after = now + _transient_backoff(row.attempts)
        elif c.recheck:
            # a once-empty board is never made permanent: try again in 30 days
            row.attempts = 0
            row.retry_after = now + EMPTY_RECHECK
        else:
            row.state = "invalid"
            row.retry_after = None
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
    """Validate due discovery candidates; returns how many were processed.

    Users with discovery enabled (no preference row = enabled) are served
    round-robin: each visit handles at most max(1, ceil(batch / users))
    candidates, rounds repeat until the global `batch` or `max_seconds`
    (measured with `clock`) is used or nobody has work left, and the starting
    user rotates from tick to tick. Per user: candidates are refreshed at
    most once an hour; due candidates are claimed one at a time (FOR UPDATE
    SKIP LOCKED + committed lease, targets > profile > catalog), the board is
    fetched with no DB transaction open and the outcome recorded; the user is
    done for the tick once it has `max_auto` active automatic companies.
    Waits `delay` seconds between network calls. A candidate whose company
    already exists becomes known without a fetch.
    """
    global _cursor
    processed = 0
    fetched = 0
    started = clock()

    def out_of_budget() -> bool:
        return processed >= batch or clock() - started >= max_seconds

    users = _rotate(_enabled_users(session_factory, user_id))
    if not users:
        return 0
    _cursor = users[0]
    quota = max(1, math.ceil(batch / len(users)))
    active = list(users)
    while active and not out_of_budget():
        still: list[int] = []
        for uid in active:
            if out_of_budget():
                break
            _maybe_refresh(session_factory, uid, clock)
            done = 0
            exhausted = False
            while done < quota and not out_of_budget():
                if _at_cap(session_factory, uid, max_auto):
                    exhausted = True
                    break
                claimed = _claim(session_factory, uid, utcnow())
                if claimed is None:
                    exhausted = True
                    break
                processed += 1
                done += 1
                if _mark_known_if_exists(session_factory, claimed, utcnow()):
                    continue
                if fetched:
                    await sleep(delay)
                fetched += 1
                result = await _validate_one(
                    session_factory, claimed, source_getter=source_getter,
                    utcnow=utcnow, max_auto=max_auto, max_attempts=max_attempts,
                )
                if result == "capped":
                    exhausted = True
                    break
            if not exhausted:
                still.append(uid)
        active = still
    return processed
