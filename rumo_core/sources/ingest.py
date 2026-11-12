"""Ingest one company's fetched jobs: upsert, duplicate detection, closing.

`ingest_jobs` runs inside the caller's transaction (the caller commits). It
loads the company's existing jobs ONCE (heavy columns deferred) and lets the
unit of work flush inserts/updates in bulk, so a board with thousands of jobs
costs a handful of statements. The unique constraint
(user_id, company_id, external_id) makes concurrent runs safe (one fails).

Rules:
- new external_id -> `open`, first_seen_at = last_seen_at = now;
- same content_hash -> only last_seen_at (a `closed` job is reopened);
- different content_hash -> every normalized field, raw and posted_at are
  rewritten and embedding/model_version/llm_filled_at/fill_attempts reset so
  the job is re-vectorized and re-filled;
- the same external_id repeated inside one fetched list: the LAST occurrence
  wins, earlier ones are ignored;
- duplicates: among the jobs present in this fetch, jobs sharing a
  fingerprint form a group; exactly one stays `open`, the others become
  `duplicate`. The survivor is, in order: a job that was already `open`
  before this batch with an unchanged fingerprint (the incumbent), then the
  smallest first_seen_at, then the smallest external_id (string compare). A
  `duplicate` whose original is gone (closed, absent or with another
  fingerprint) is thus promoted to `open`. Note: the incumbent rule goes
  first on purpose (an already-open job keeps its slot even if an older,
  changed or reappearing job now shares its fingerprint), so open jobs do not
  flip between open and duplicate across runs;
- jobs `open` or `duplicate` absent from the list -> `closed` (closed_at=now);
- an EMPTY effective list (also when every item was filtered out, e.g. an
  oversized external_id) while the company has >= SUSPICIOUS_EMPTY_MIN_OPEN
  open jobs raises SuspiciousEmptyResult before anything is touched.

One bad posting never blocks the company: NUL characters (rejected by Postgres
in Text and JSONB) are stripped from every text field and from `raw` first,
and the salary extractor treats amounts the column cannot hold as unknown.
Should the DB still reject a job (DataError), the bulk pass (run in a
savepoint) is rolled back and redone with one savepoint per job: each rejected
job is skipped (logged by external_id, counted in `skipped`); a skipped job
that already existed keeps its previous row and is not closed (it is still on
the board). IntegrityError (a concurrent run) still propagates.

Counters: created/updated/unchanged partition the jobs present in the list
(minus the skipped ones); closed, reopened (closed -> open) and duplicates
(jobs that BECAME duplicate in this batch) count status transitions, so a
repeated identical ingest reports only `unchanged`.
"""

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, defer

from rumo_core.models import Company, Job
from rumo_core.sources.base import RawJob
from rumo_core.sources.normalize import NormalizedJob, normalize_job

logger = logging.getLogger("rumo_core.sources.ingest")

SUSPICIOUS_EMPTY_MIN_OPEN = 5
_MAX_EXTERNAL_ID = 255  # Job.external_id column size
_MAX_CITY = 255  # Job.city column size
_MAX_COUNTRY = 64  # Job.country column size


class SuspiciousEmptyResult(Exception):
    """The source returned no jobs for a company that had many open ones."""


@dataclass
class IngestResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    closed: int = 0
    reopened: int = 0
    duplicates: int = 0
    skipped: int = 0  # jobs the DB rejected (left as they were / not created)


def _strip_nul(value):
    """`value` with every NUL removed from its strings (dict keys included)."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_strip_nul(v) for v in value]
    return value


def _sanitize(r: RawJob) -> RawJob:
    return dataclasses.replace(
        r,
        external_id=_strip_nul(r.external_id),
        title=_strip_nul(r.title),
        url=_strip_nul(r.url),
        location_text=_strip_nul(r.location_text),
        description_html=_strip_nul(r.description_html),
        department=_strip_nul(r.department),
        raw=_strip_nul(r.raw),
    )


def _apply(job: Job, raw: RawJob, n: NormalizedJob) -> None:
    job.url = raw.url
    job.title = raw.title
    job.location_text = raw.location_text
    job.description = n.description
    job.raw = raw.raw
    job.posted_at = raw.posted_at
    job.country = n.country[:_MAX_COUNTRY] if n.country else None
    job.city = n.city[:_MAX_CITY] if n.city else None
    job.work_model = n.work_model
    job.seniority = n.seniority
    job.language = n.language
    job.department = n.department
    job.salary_min = n.salary_min
    job.salary_max = n.salary_max
    job.salary_currency = n.salary_currency
    job.salary_period = n.salary_period
    job.salary_known = n.salary_known
    job.content_hash = n.content_hash
    job.fingerprint = n.fingerprint


def ingest_jobs(
    session: Session, user_id: int, company: Company, raws: list[RawJob], *, now: datetime
) -> IngestResult:
    # last occurrence of a repeated external_id wins
    by_id: dict[str, RawJob] = {}
    for r in raws:
        r = _sanitize(r)
        if len(r.external_id) > _MAX_EXTERNAL_ID:
            logger.warning("company %s: skipping job with oversized external_id", company.id)
            continue
        by_id[r.external_id] = r

    # guard on the EFFECTIVE list: "every item filtered out" counts as empty
    if not by_id:
        open_count = session.scalar(
            select(func.count()).select_from(Job).where(
                Job.user_id == user_id, Job.company_id == company.id, Job.status == "open"
            )
        )
        if open_count >= SUSPICIOUS_EMPTY_MIN_OPEN:
            raise SuspiciousEmptyResult(
                f"A fonte devolveu uma lista vazia, mas a empresa tem {open_count} "
                "vagas abertas; nada foi fechado."
            )

    try:
        with session.begin_nested():
            return _ingest(session, user_id, company, by_id, now, isolate=False)
    except DataError:
        # the savepoint rollback expunged the new jobs and expired the changed
        # ones: redo the pass job by job to find and skip the rejected ones
        logger.warning("company %s: bulk ingest rejected; retrying job by job", company.id)
    return _ingest(session, user_id, company, by_id, now, isolate=True)


def _ingest(
    session: Session, user_id: int, company: Company, by_id: dict[str, RawJob],
    now: datetime, *, isolate: bool,
) -> IngestResult:
    existing: dict[str, Job] = {
        j.external_id: j
        for j in session.scalars(
            select(Job)
            .where(Job.user_id == user_id, Job.company_id == company.id)
            .options(defer(Job.embedding), defer(Job.raw), defer(Job.description))
        )
    }
    prev_status = {eid: j.status for eid, j in existing.items()}
    res = IngestResult()
    present: list[Job] = []
    incumbent: set[str] = set()


    for eid, r in by_id.items():
        n = normalize_job(r)
        if not isolate:
            _upsert(session, user_id, company, existing, eid, r, n, now, res, present, incumbent)
            continue
        counts, saved, inc = dataclasses.replace(res), [], set()
        try:
            with session.begin_nested():
                _upsert(session, user_id, company, existing, eid, r, n, now, counts, saved, inc)
                session.flush()
        except DataError:
            # rolled back: a new job was expunged, a changed one reverted to its row
            logger.warning("company %s: skipping job %r the database rejected",
                           company.id, eid[:64])
            if eid not in prev_status:
                existing.pop(eid, None)
            res.skipped += 1
            continue
        res = counts
        present += saved
        incumbent |= inc

    # one survivor per fingerprint among the present jobs
    groups: dict[str, list[Job]] = {}
    for job in present:
        groups.setdefault(job.fingerprint, []).append(job)
    for members in groups.values():
        survivor = min(
            members,
            key=lambda j: (j.external_id not in incumbent, j.first_seen_at, j.external_id),
        )
        for job in members:
            before = prev_status.get(job.external_id)
            job.status = "open" if job is survivor else "duplicate"
            if before == "closed":
                job.closed_at = None
            if job.status == "open" and before == "closed":
                res.reopened += 1
            if job.status == "duplicate" and before != "duplicate":
                res.duplicates += 1

    for eid, job in existing.items():
        if eid not in by_id and job.status in ("open", "duplicate"):
            job.status = "closed"
            job.closed_at = now
            res.closed += 1

    company.open_jobs = sum(1 for j in existing.values() if j.status == "open")
    session.flush()
    return res


def _upsert(
    session: Session, user_id: int, company: Company, existing: dict[str, Job], eid: str,
    r: RawJob, n: NormalizedJob, now: datetime, res: IngestResult, present: list[Job],
    incumbent: set[str],
) -> None:
    job = existing.get(eid)
    if job is None:
        job = Job(
            user_id=user_id, company_id=company.id, external_id=eid,
            status="open", first_seen_at=now, last_seen_at=now, fill_attempts=0,
        )
        _apply(job, r, n)
        session.add(job)
        existing[eid] = job
        res.created += 1
    elif job.content_hash == n.content_hash:
        job.last_seen_at = now
        res.unchanged += 1
        if job.status == "open":
            incumbent.add(eid)
    else:
        same_fp = job.fingerprint == n.fingerprint
        _apply(job, r, n)
        job.embedding = None
        job.model_version = None
        job.llm_filled_at = None
        job.fill_attempts = 0
        job.last_seen_at = now
        res.updated += 1
        if job.status == "open" and same_fp:
            incumbent.add(eid)
    present.append(job)
