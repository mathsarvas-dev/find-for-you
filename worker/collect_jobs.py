"""Worker jobs: job collection from the companies' sources."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from rumo_core.models import Company
from rumo_core.sources import get_source
from rumo_core.sources.base import (
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)
from rumo_core.sources.ingest import SuspiciousEmptyResult, ingest_jobs

logger = logging.getLogger("worker.collect_jobs")

MSG_NOT_FOUND = "Quadro não encontrado no Greenhouse."
MSG_UNAVAILABLE = "Fonte indisponível no momento."
MSG_BAD_RESPONSE = "Resposta inesperada da fonte."
MSG_SUSPICIOUS_EMPTY = "A fonte devolveu uma lista vazia de vagas; mantive as vagas abertas."
MSG_UNKNOWN_SOURCE = "Fonte desconhecida."
MSG_UNEXPECTED = "Falha inesperada na coleta."

# Claiming pushes next_run_at this far ahead (committed before the fetch) so
# another worker or tick cannot take the same company during a slow fetch. A
# worker that dies or is cancelled mid-fetch leaves the company due again after
# the lease.
CLAIM_LEASE = timedelta(minutes=30)
BACKOFF_BASE = timedelta(minutes=15)

# expected failures: short PT-BR message; only class name + company id logged
_EXPECTED = (
    (SourceNotFound, MSG_NOT_FOUND),
    (SourceUnavailable, MSG_UNAVAILABLE),
    (SourceBadResponse, MSG_BAD_RESPONSE),
    (SuspiciousEmptyResult, MSG_SUSPICIOUS_EMPTY),
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedCompany:
    company_id: int
    user_id: int
    source: str
    identifier: str


class _UnknownSource(Exception):
    pass


def _claim_company(
    session_factory, user_id: int | None, now: datetime
) -> ClaimedCompany | None:
    """Claim the most overdue due company and commit a lease on it."""
    with session_factory() as s:
        stmt = select(Company).where(
            Company.active.is_(True),
            or_(Company.next_run_at.is_(None), Company.next_run_at <= now),
        )
        if user_id is not None:
            stmt = stmt.where(Company.user_id == user_id)
        row = s.execute(
            stmt.order_by(Company.next_run_at.asc().nulls_first(), Company.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.next_run_at = now + CLAIM_LEASE
        claimed = ClaimedCompany(row.id, row.user_id, row.source, row.identifier)
        s.commit()
        return claimed


def _backoff(failures: int, interval_hours: int) -> timedelta:
    # exponent bounded so huge failure counts cannot overflow timedelta
    delay = BACKOFF_BASE * 2 ** min(max(failures - 1, 0), 20)
    return min(delay, timedelta(hours=interval_hours))


def _lock_company(s, company_id: int) -> Company | None:
    return s.execute(
        select(Company).where(Company.id == company_id).with_for_update()
    ).scalar_one_or_none()


def _record_success(session_factory, c: ClaimedCompany, raws, now, interval_hours) -> None:
    """Ingest + health update in ONE transaction."""
    with session_factory() as s:
        company = _lock_company(s, c.company_id)
        if company is None:  # deleted meanwhile
            return
        ingest_jobs(s, c.user_id, company, raws, now=now)
        company.last_run_at = now
        company.last_success_at = now
        company.last_error = None
        company.consecutive_failures = 0
        company.next_run_at = now + timedelta(hours=interval_hours)
        s.commit()


def _record_failure(session_factory, company_id: int, msg: str, now, interval_hours) -> None:
    with session_factory() as s:
        company = _lock_company(s, company_id)
        if company is None:
            return
        company.consecutive_failures += 1
        company.last_run_at = now
        company.last_error = msg
        company.next_run_at = now + _backoff(company.consecutive_failures, interval_hours)
        s.commit()


async def _collect_one(session_factory, c: ClaimedCompany, *, source_getter, clock,
                       interval_hours: int) -> None:
    try:
        try:
            source = source_getter(c.source)
        except KeyError:
            raise _UnknownSource() from None
        raws = await source.fetch_jobs(c.identifier)  # no DB transaction held
        _record_success(session_factory, c, raws, clock(), interval_hours)
    except _UnknownSource:
        logger.warning("company %s: unknown source", c.company_id)
        _record_failure(session_factory, c.company_id, MSG_UNKNOWN_SOURCE, clock(),
                        interval_hours)
    except Exception as exc:
        for cls, msg in _EXPECTED:
            if isinstance(exc, cls):
                # class name and company id only: the message may echo a response body
                logger.warning("company %s collect failed (%s)", c.company_id,
                               type(exc).__name__)
                break
        else:
            msg = MSG_UNEXPECTED
            logger.exception("company %s unexpected collect failure", c.company_id)
        _record_failure(session_factory, c.company_id, msg, clock(), interval_hours)
    # CancelledError (BaseException) propagates: the committed lease stays, so
    # the company is retried once it expires; the ingest transaction, if any,
    # was rolled back by the session context manager.


async def collect_companies(
    session_factory,
    *,
    source_getter=get_source,
    user_id: int | None = None,
    clock=_utcnow,
    sleep=asyncio.sleep,
    interval_hours: int,
    delay_seconds: float,
) -> int:
    """Collect every due company, one at a time; returns how many were processed.

    Due: active and next_run_at NULL or <= now, most overdue (NULL) first.
    Each company is claimed with FOR UPDATE SKIP LOCKED plus a committed lease,
    fetched without a DB transaction, then ingested and its health updated in
    one transaction. Success schedules the next run after interval_hours;
    failure backs off 15 min x 2^(failures-1), capped at interval_hours.
    Waits delay_seconds between companies (not after the last).
    """
    processed = 0
    while True:
        claimed = _claim_company(session_factory, user_id, clock())
        if claimed is None:
            return processed
        if processed:
            await sleep(delay_seconds)
        await _collect_one(
            session_factory, claimed, source_getter=source_getter, clock=clock,
            interval_hours=interval_hours,
        )
        processed += 1
