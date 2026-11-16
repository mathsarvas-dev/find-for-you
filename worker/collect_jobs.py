"""Worker jobs: job collection from the companies' sources."""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from rumo_core.embeddings.base import Embedder
from rumo_core.llm.base import LLMError, LLMProvider, LLMRateLimited
from rumo_core.matching.gate import apply_vec_gate
from rumo_core.models import Company, Job, UserPreference
from rumo_core.sources import get_source
from rumo_core.sources.base import (
    SourceBadResponse,
    SourceBlocked,
    SourceNotFound,
    SourceUnavailable,
    source_rank_for,
)
from rumo_core.sources.careers import MSG_NO_STRUCTURED, EmbeddedAtsFound, NoStructuredData
from rumo_core.sources.embed_text import job_embedding_text
from rumo_core.sources.fill import JobView, fill_fields, validate_fill
from rumo_core.sources.funnel import funnel_filters
from rumo_core.sources.ingest import IngestPolicy, SuspiciousEmptyResult, ingest_jobs
from rumo_core.sources.normalize import has_pay_context
from rumo_core.sources.portals.gate import check, portal_for_url
from rumo_core.sources.portals.state import load_states, mark_blocked_by_site
from rumo_core.sources.robots import RobotsDisallowed
from worker import jobs as _chunk_jobs
from worker.llm_priority import interactive_llm_busy

logger = logging.getLogger("worker.collect_jobs")

MSG_NOT_FOUND = "Quadro não encontrado no Greenhouse."
MSG_UNAVAILABLE = "Fonte indisponível no momento."
MSG_BAD_RESPONSE = "Resposta inesperada da fonte."
MSG_SUSPICIOUS_EMPTY = "A fonte devolveu uma lista vazia de vagas; mantive as vagas abertas."
MSG_UNKNOWN_SOURCE = "Fonte desconhecida."
MSG_UNEXPECTED = "Falha inesperada na coleta."
MSG_BLOCKED = "O site recusou nosso acesso repetidamente; a coleta desta fonte foi suspensa."
MSG_ROBOTS = "O robots.txt do site não permite a coleta desta página."
MSG_CONVERTED = "Página de carreira usa {ats}; a empresa passou a ser coletada direto pela API."
_ATS_LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters", "breezy": "Breezy", "recruitee": "Recruitee",
    "personio": "Personio", "teamtailor": "Teamtailor", "bamboohr": "BambooHR",
    "pinpoint": "Pinpoint",
}

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


def company_policy(s, company: Company, c: ClaimedCompany) -> IngestPolicy:
    """Origin, rank and funnel filters for one company's fetch.

    collect_mode 'funnel' applies the user's hard filters at intake; 'all'
    keeps every job full.
    """
    origin = "site" if c.source == "site" else "ats"
    filters = funnel_filters(s, c.user_id) if company.collect_mode == "funnel" else None
    return IngestPolicy(origin=origin, source_rank=source_rank_for(c.source), filters=filters)


def _record_success(session_factory, c: ClaimedCompany, raws, now, interval_hours,
                    listed_ids: frozenset[str] = frozenset()) -> None:
    """Ingest + health update in ONE transaction."""
    with session_factory() as s:
        company = _lock_company(s, c.company_id)
        if company is None:  # deleted meanwhile
            return
        policy = replace(company_policy(s, company, c), listed_ids=listed_ids)
        ingest_jobs(s, c.user_id, company, raws, now=now, policy=policy)
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


def _pause(session_factory, company_id: int, msg: str, now) -> None:
    """Stop collecting a company; the user can re-enable it."""
    with session_factory() as s:
        company = _lock_company(s, company_id)
        if company is None:
            return
        company.active = False
        company.last_run_at = now
        company.last_error = msg
        s.commit()


def _pause_blocked(session_factory, c: ClaimedCompany, now) -> None:
    """Pause after repeated 403/429; a portal host is also marked as refusing us."""
    with session_factory() as s:
        company = _lock_company(s, c.company_id)
        if company is None:
            return
        company.active = False
        company.last_run_at = now
        company.last_error = MSG_BLOCKED
        portal = portal_for_url(c.identifier)
        if portal is not None:
            mark_blocked_by_site(s, c.user_id, portal.id, now)
        s.commit()


def _convert_company(session_factory, c: ClaimedCompany, found: EmbeddedAtsFound, now) -> None:
    """A careers page embeds an ATS board: collect that board directly from now on."""
    msg = MSG_CONVERTED.format(ats=_ATS_LABELS.get(found.source, found.source))
    with session_factory() as s:
        company = _lock_company(s, c.company_id)
        if company is None:
            return
        taken = s.scalar(
            select(Company.id).where(
                Company.user_id == c.user_id, Company.source == found.source,
                Company.identifier == found.identifier,
            )
        )
        company.last_run_at = now
        company.last_error = msg
        if taken is None:
            company.source = found.source
            company.identifier = found.identifier
            company.next_run_at = now
        else:
            company.active = False
        s.commit()


def _gate_message(session_factory, c: ClaimedCompany) -> str | None:
    """Why a portal page may not be collected now (None = allowed)."""
    portal = portal_for_url(c.identifier)
    if portal is None:
        return None
    with session_factory() as s:
        state = load_states(s, c.user_id).get(portal.id)
    verdict = check(portal.id, c.identifier, state=state)
    return None if verdict.allowed else verdict.message


def _known_external_ids(session_factory, company_id: int) -> frozenset[str]:
    """Jobs still on the board (stubs excluded: they must be judged again)."""
    with session_factory() as s:
        return frozenset(s.scalars(
            select(Job.external_id).where(
                Job.company_id == company_id, Job.status.in_(("open", "duplicate")),
                Job.intake != "stub",
            )
        ))


async def _collect_site(session_factory, c: ClaimedCompany, source, clock,
                        interval_hours: int) -> None:
    msg = _gate_message(session_factory, c)
    if msg is not None:
        _record_failure(session_factory, c.company_id, msg, clock(), interval_hours)
        return
    known = _known_external_ids(session_factory, c.company_id)
    fetch = await source.fetch_listing(c.identifier, known_ids=known)  # no transaction held
    await asyncio.to_thread(
        _record_success, session_factory, c, fetch.raws, clock(), interval_hours,
        fetch.listed_ids,
    )


async def _collect_one(session_factory, c: ClaimedCompany, *, source_getter, clock,
                       interval_hours: int) -> None:
    try:
        try:
            source = source_getter(c.source)
        except KeyError:
            raise _UnknownSource() from None
        if c.source == "site":
            await _collect_site(session_factory, c, source, clock, interval_hours)
        else:
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
    except EmbeddedAtsFound as exc:
        logger.info("company %s: careers page embeds %s", c.company_id, exc.source)
        _convert_company(session_factory, c, exc, clock())
    except SourceBlocked:
        logger.warning("company %s: source refused access repeatedly", c.company_id)
        _pause_blocked(session_factory, c, clock())
    except NoStructuredData:
        logger.warning("company %s: no structured job data", c.company_id)
        _pause(session_factory, c.company_id, MSG_NO_STRUCTURED, clock())
    except RobotsDisallowed:
        logger.warning("company %s: robots.txt disallows the page", c.company_id)
        _record_failure(session_factory, c.company_id, MSG_ROBOTS, clock(), interval_hours)
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
            Job.intake != "stub",
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
            by_owner: dict[int, list[int]] = {}
            for job, _name in rows:
                by_owner.setdefault(job.user_id, []).append(job.id)
            s.commit()
            _jobs_embed_failures = 0
            total += len(rows)
        _apply_floors(session_factory, by_owner, embedder.model_version)
    return total


def _apply_floors(session_factory, by_owner: dict[int, list[int]], model_version: str) -> None:
    """Cut the just-vectorised jobs of users who set a vector floor (own transaction)."""
    with session_factory() as s:
        for owner, ids in by_owner.items():
            floor = s.scalar(
                select(UserPreference.adherence_vec_floor).where(UserPreference.user_id == owner)
            )
            if floor is not None:
                apply_vec_gate(s, owner, model_version=model_version, floor=floor, job_ids=ids)
        s.commit()


# --- LLM fill of missing fields -----------------------------------------------

MAX_FILL_ATTEMPTS = 3
FILL_RATE_LIMIT_PAUSE_SECONDS = 60.0
_FILL_WINDOW_FACTOR = 10
_FILL_MAX_WINDOWS = 5

_fill_warned_no_llm = False
_fill_pause_until = 0.0


def _reset_fill_state() -> None:
    """Reset the fill job's in-process state (used by tests)."""
    global _fill_warned_no_llm, _fill_pause_until
    _fill_warned_no_llm = False
    _fill_pause_until = 0.0


def _needs_fill(job: Job) -> bool:
    return job.work_model == "unknown" or (
        not job.salary_known and has_pay_context(job.description)
    )


def _view(job: Job) -> JobView:
    return JobView(
        title=job.title, location_text=job.location_text, department=job.department,
        description=job.description, work_model=job.work_model,
        salary_known=job.salary_known,
    )


def _select_fill_candidates(
    session_factory, batch: int, user_id: int | None
) -> tuple[list[tuple[int, str]], int]:
    """([(job_id, content_hash)] needing the LLM, number settled without it).

    Looks at windows of the newest candidates (cheap SQL predicates), keeps
    those that really need the LLM (decided in Python) and marks the rest
    filled so they leave the candidate set. The commit releases the locks.
    """
    picked: list[tuple[int, str]] = []
    skipped = 0
    window = batch * _FILL_WINDOW_FACTOR
    seen: set[int] = set()  # picked rows stay unmarked: never re-pick them this tick
    for _ in range(_FILL_MAX_WINDOWS):
        with session_factory() as s:
            # with a vector floor the LLM only sees jobs that already have a vector
            stmt = (
                select(Job)
                .outerjoin(UserPreference, UserPreference.user_id == Job.user_id)
                .where(
                    Job.status == "open",
                    Job.intake == "full",
                    Job.llm_filled_at.is_(None),
                    Job.fill_attempts < MAX_FILL_ATTEMPTS,
                    or_(Job.work_model == "unknown", Job.salary_known.is_(False)),
                    or_(
                        UserPreference.adherence_vec_floor.is_(None),
                        Job.embedding.is_not(None),
                    ),
                )
            )
            if user_id is not None:
                stmt = stmt.where(Job.user_id == user_id)
            if seen:
                stmt = stmt.where(Job.id.notin_(seen))
            rows = s.execute(
                stmt.order_by(Job.first_seen_at.desc(), Job.id.desc())
                .limit(window)
                .with_for_update(of=Job, skip_locked=True)
            ).scalars().all()
            now = _utcnow()
            for job in rows:
                if not _needs_fill(job):
                    job.llm_filled_at = now
                    skipped += 1
                elif len(picked) < batch:
                    picked.append((job.id, job.content_hash))
                    seen.add(job.id)
            s.commit()
        if len(picked) >= batch or len(rows) < window:
            break
    return picked, skipped


def _load_fill_view(session_factory, job_id: int, content_hash: str) -> JobView | None:
    with session_factory() as s:
        job = s.get(Job, job_id)
        if (
            job is None or job.status != "open" or job.intake != "full"
            or job.llm_filled_at is not None
            or job.fill_attempts >= MAX_FILL_ATTEMPTS or job.content_hash != content_hash
        ):
            return None
        return _view(job)


def _write_fill(session_factory, job_id: int, content_hash: str, result) -> bool:
    """Persist the validated result in a fresh transaction; False (result
    discarded) when ingest rewrote the job meanwhile."""
    with session_factory() as s:
        job = s.execute(
            select(Job).where(Job.id == job_id).with_for_update()
        ).scalar_one_or_none()
        if (job is None or job.content_hash != content_hash or job.status != "open"
                or job.intake != "full"):
            s.rollback()
            return False
        validated = validate_fill(result, job.description, _view(job))
        if validated.work_model != "unknown":
            job.work_model = validated.work_model
        if validated.salary_min is not None:
            job.salary_min = validated.salary_min
            job.salary_max = validated.salary_max
            job.salary_currency = validated.salary_currency
            job.salary_period = validated.salary_period
            job.salary_known = True
        job.llm_filled_at = _utcnow()
        s.commit()
        return True


def _bump_attempts(session_factory, job_id: int, content_hash: str) -> None:
    with session_factory() as s:
        s.execute(
            update(Job)
            .where(Job.id == job_id, Job.content_hash == content_hash)
            .values(fill_attempts=Job.fill_attempts + 1)
        )
        s.commit()


async def fill_missing_job_fields(
    session_factory,
    llm: LLMProvider | None,
    *,
    batch: int,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Ask the LLM for the work model / salary the rules left unknown; returns
    how many jobs went through the LLM (jobs settled without it do not count).

    No LLM (no key): warns once, does nothing. Rate limit: pauses the job for
    FILL_RATE_LIMIT_PAUSE_SECONDS, stops the batch, counts no attempt. Invalid
    response / LLM error / unexpected failure: counts an attempt (after 3 the
    job is no longer tried). Never logs job text or the LLM's answer.
    """
    global _fill_warned_no_llm, _fill_pause_until
    if llm is None:
        if not _fill_warned_no_llm:
            logger.warning("GROQ_API_KEY not set: job field fill disabled")
            _fill_warned_no_llm = True
        return 0
    if clock() < _fill_pause_until:
        return 0
    if interactive_llm_busy(session_factory):
        return 0  # a resume import / interview turn keeps the Groq budget
    picked, skipped = _select_fill_candidates(session_factory, batch, user_id)
    if skipped:
        logger.info("fill: %d jobs settled without the LLM", skipped)
    done = 0
    for job_id, content_hash in picked:
        view = _load_fill_view(session_factory, job_id, content_hash)
        if view is None:
            continue
        try:
            result = await fill_fields(llm, view)  # no DB transaction held
            _write_fill(session_factory, job_id, content_hash, result)
        except LLMRateLimited:
            _fill_pause_until = clock() + FILL_RATE_LIMIT_PAUSE_SECONDS
            logger.warning(
                "LLM rate limited; pausing job fill for %ds", FILL_RATE_LIMIT_PAUSE_SECONDS
            )
            return done
        except LLMError as exc:
            # class name and job id only: the message may echo the posting
            logger.warning("job %s fill failed (%s)", job_id, type(exc).__name__)
            _bump_attempts(session_factory, job_id, content_hash)
        except Exception as exc:  # noqa: BLE001 - isolate one job's failure
            logger.warning("job %s unexpected fill failure (%s)", job_id, type(exc).__name__)
            _bump_attempts(session_factory, job_id, content_hash)
        done += 1
    return done
