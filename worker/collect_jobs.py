"""Worker jobs: job collection from the companies' sources."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from rumo_core.embeddings.base import Embedder
from rumo_core.models import Company, Job
from rumo_core.sources import get_source
from rumo_core.sources.base import (
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)
from rumo_core.sources.embed_text import job_embedding_text
from rumo_core.sources.ingest import SuspiciousEmptyResult, ingest_jobs
from worker import jobs as _chunk_jobs

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
        # normalize + ingest are CPU/DB-bound: run them off the event loop. The
        # thread opens its OWN session (nothing is shared across threads).
        await asyncio.to_thread(
            _record_success, session_factory, c, raws, clock(), interval_hours
        )
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
    # the company is retried once it expires. Cancelled while the ingest thread
    # runs: that thread still finishes its single transaction (commit or
    # rollback as a whole), so no half state is left either way.


async def collect_companies(
    session_factory,
    *,
    source_getter=get_source,
    user_id: int | None = None,
    clock=_utcnow,
    sleep=asyncio.sleep,
    interval_hours: int,
    delay_seconds: float,
    max_companies: int | None = None,
    max_seconds: float | None = None,
) -> int:
    """Collect due companies, one at a time; returns how many were processed.

    Stops claiming once `max_companies` were processed or `max_seconds` (time
    since the call started, measured with `clock`) has elapsed; a company
    already claimed is always finished. Bounding a run keeps the sequential
    worker loop responsive for the other jobs.

    Due: active and next_run_at NULL or <= now, most overdue (NULL) first.
    Each company is claimed with FOR UPDATE SKIP LOCKED plus a committed lease,
    fetched without a DB transaction, then ingested and its health updated in
    one transaction. Success schedules the next run after interval_hours;
    failure backs off 15 min x 2^(failures-1), capped at interval_hours.
    Waits delay_seconds between companies (not after the last).
    """
    processed = 0
    started = clock()
    while True:
        if max_companies is not None and processed >= max_companies:
            return processed
        now = clock()
        if max_seconds is not None and (now - started).total_seconds() >= max_seconds:
            return processed
        claimed = _claim_company(session_factory, user_id, now)
        if claimed is None:
            return processed
        if processed:
            await sleep(delay_seconds)
        await _collect_one(
            session_factory, claimed, source_getter=source_getter, clock=clock,
            interval_hours=interval_hours,
        )
        processed += 1


# --- job embedding -------------------------------------------------------

# Separate backoff state from the chunk embedding job (same constants).
_jobs_embed_next_try = 0.0
_jobs_embed_failures = 0


def _reset_embed_state() -> None:
    """Reset the job-embedding backoff state (used by tests)."""
    global _jobs_embed_next_try, _jobs_embed_failures
    _jobs_embed_next_try = 0.0
    _jobs_embed_failures = 0


def pending_jobs_stmt(model_version: str, user_id: int | None = None):
    """Open jobs with no embedding or one from another model version."""
    stmt = (
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(
            Job.status == "open",
            or_(Job.embedding.is_(None), Job.model_version.is_distinct_from(model_version)),
        )
    )
    if user_id is not None:
        stmt = stmt.where(Job.user_id == user_id)
    return stmt.order_by(Job.first_seen_at.desc(), Job.id)


async def embed_pending_jobs(
    session_factory,
    embedder: Embedder,
    batch_size: int,
    *,
    user_id: int | None = None,
    clock=time.monotonic,
    max_batches: int = 1,
) -> int:
    """Embed open jobs needing work; at most `max_batches` batches per call
    (one commit per batch), newest first. Returns how many were vectorised.

    The shared EMBED_LOCK serialises this with chunk embedding (one CPU-bound
    model). On embedder failure only the exception class and batch size are
    logged (never job text) and the job backs off exponentially (5s doubling,
    cap 300s, reset on success) instead of raising.
    """
    global _jobs_embed_next_try, _jobs_embed_failures
    if clock() < _jobs_embed_next_try:
        return 0
    total = 0
    for _ in range(max_batches):
        with session_factory() as s:
            rows = s.execute(
                pending_jobs_stmt(embedder.model_version, user_id)
                .limit(batch_size)
                .with_for_update(of=Job, skip_locked=True)
            ).all()
            if not rows:
                return total
            texts = [job_embedding_text(job, name) for job, name in rows]
            try:
                async with _chunk_jobs.EMBED_LOCK:
                    vectors = await asyncio.to_thread(embedder.embed, texts)
            except Exception as exc:
                s.rollback()
                delay = min(
                    _chunk_jobs.EMBED_BACKOFF_BASE * 2**_jobs_embed_failures,
                    _chunk_jobs.EMBED_BACKOFF_CAP,
                )
                _jobs_embed_failures += 1
                _jobs_embed_next_try = clock() + delay
                logger.error(
                    "job embedding failed (%s, batch of %d); backing off %ds",
                    type(exc).__name__, len(rows), delay,
                )
                return total
            for (job, _name), v in zip(rows, vectors, strict=True):
                job.embedding = v
                job.model_version = embedder.model_version
            s.commit()
            _jobs_embed_failures = 0
            total += len(rows)
    return total
