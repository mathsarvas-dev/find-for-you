"""Worker job: LLM score of the pending matches (`score_matches`).

Same pattern as `collect_jobs.fill_missing_job_fields`: candidates are picked
with FOR UPDATE SKIP LOCKED and committed before the slow LLM call; the result
is written in a fresh transaction after re-reading the job and the user's
current profile version under row lock. Never logs job text or LLM output.
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import aliased

from rumo_core.llm.base import LLMError, LLMProvider, LLMRateLimited
from rumo_core.matching.ai_score import JobView, MatchInvalid, score_job
from rumo_core.models import Company, Job, Match, Profile
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile

logger = logging.getLogger("worker.match_jobs")

RATE_LIMIT_PAUSE_SECONDS = 60.0

_warned_no_llm = False
_pause_until = 0.0
# match ids between selection and write in THIS process: two overlapping
# ticks never send the same row to the LLM (across processes, SKIP LOCKED
# covers the selection and the write re-check makes a duplicate harmless)
_in_flight: set[int] = set()


def _reset_state() -> None:
    """Reset the job's in-process state (used by tests)."""
    global _warned_no_llm, _pause_until
    _warned_no_llm = False
    _pause_until = 0.0
    _in_flight.clear()


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Picked:
    match_id: int
    content_hash: str  # job hash at selection time


@dataclass(frozen=True)
class _Work:
    profile: CanonicalProfile
    job: JobView
    content_hash: str


def _current_version(user_col):
    p = aliased(Profile)
    return select(func.max(p.version)).where(p.user_id == user_col).scalar_subquery()


def _select_candidates(session_factory, batch: int, max_attempts: int,
                       user_id: int | None) -> list[_Picked]:
    """Pending rows of the current profile version whose job is open, best
    vec_score first. The commit releases the locks."""
    with session_factory() as s:
        stmt = (
            select(Match.id, Job.content_hash)
            .join(Job, Job.id == Match.job_id)
            .where(
                Match.state == "pendente",
                Match.attempts < max_attempts,
                Job.status == "open",
                Job.user_id == Match.user_id,
                Match.profile_version == _current_version(Match.user_id),
            )
        )
        if user_id is not None:
            stmt = stmt.where(Match.user_id == user_id)
        if _in_flight:
            stmt = stmt.where(Match.id.notin_(_in_flight))
        rows = s.execute(
            stmt.order_by(Match.vec_score.desc(), Match.id)
            .limit(batch)
            .with_for_update(of=Match, skip_locked=True)
        ).all()
        s.commit()
    return [_Picked(mid, h) for mid, h in rows]


def _job_view(job: Job, company_name: str | None) -> JobView:
    return JobView(
        title=job.title, company_name=company_name, location_text=job.location_text,
        work_model=job.work_model, seniority=job.seniority, department=job.department,
        description=job.description,
    )


def _load(session_factory, p: _Picked) -> _Work | None:
    """Job + the profile of the row's version, or None if no longer workable."""
    with session_factory() as s:
        m = s.get(Match, p.match_id)
        if m is None or m.state != "pendente":
            return None
        row = s.execute(
            select(Job, Company.name)
            .join(Company, Company.id == Job.company_id)
            .where(Job.id == m.job_id, Job.user_id == m.user_id)
        ).first()
        if row is None:
            return None
        job, company_name = row
        if job.status != "open" or job.content_hash != p.content_hash:
            return None
        prof = s.execute(
            select(Profile).where(
                Profile.user_id == m.user_id, Profile.version == m.profile_version)
        ).scalar_one_or_none()
        if prof is None:
            return None
        return _Work(
            profile=CanonicalProfile.model_validate(prof.data),
            job=_job_view(job, company_name),
            content_hash=job.content_hash,
        )


def _write(session_factory, match_id: int, content_hash: str, result) -> bool:
    """Persist in a fresh transaction; False (discarded, row left as is) when
    the job changed or closed, or the profile has a newer version."""
    with session_factory() as s:
        m = s.execute(
            select(Match).where(Match.id == match_id).with_for_update()
        ).scalar_one_or_none()
        if m is None or m.state != "pendente":
            s.rollback()
            return False
        job = s.execute(
            select(Job).where(Job.id == m.job_id).with_for_update()
        ).scalar_one_or_none()
        current = get_current_profile(s, m.user_id)
        if (
            job is None or job.status != "open" or job.content_hash != content_hash
            or current is None or current.version != m.profile_version
        ):
            s.rollback()
            return False
        m.ai_score = result.score
        m.rationale = result.rationale
        m.strengths = list(result.strengths)
        m.gaps = list(result.gaps)
        m.scored_at = _utcnow()
        m.content_hash = content_hash
        m.state = "ok"
        m.error = None
        s.commit()
        return True


def _bump_attempts(session_factory, match_id: int, max_attempts: int,
                   exc: BaseException) -> None:
    """attempts+1; at max_attempts the row becomes 'erro' with the class name
    only (no job or LLM text)."""
    reached = Match.attempts + 1 >= max_attempts
    with session_factory() as s:
        s.execute(
            update(Match)
            .where(Match.id == match_id, Match.state == "pendente")
            .values(
                attempts=Match.attempts + 1,
                state=case((reached, "erro"), else_=Match.state),
                error=case((reached, type(exc).__name__), else_=Match.error),
            )
        )
        s.commit()


async def score_pending_matches(
    session_factory,
    llm: LLMProvider | None,
    *,
    batch: int,
    max_attempts: int,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Score up to `batch` pending matches with the LLM; returns how many went
    through the LLM.

    No LLM (no key): warns once, does nothing. Rate limit: pauses for
    RATE_LIMIT_PAUSE_SECONDS, stops the batch, counts no attempt.
    MatchInvalid / LLM error / unexpected failure: counts an attempt; at
    `max_attempts` the row becomes 'erro' with the exception class name.
    """
    global _warned_no_llm, _pause_until
    if llm is None:
        if not _warned_no_llm:
            logger.warning("GROQ_API_KEY not set: match scoring disabled")
            _warned_no_llm = True
        return 0
    if clock() < _pause_until:
        return 0
    picked = _select_candidates(session_factory, batch, max_attempts, user_id)
    _in_flight.update(p.match_id for p in picked)
    done = 0
    try:
        for p in picked:
            try:
                work = _load(session_factory, p)
                if work is None:
                    continue
                result = await score_job(llm, work.profile, work.job)  # no DB tx held
                _write(session_factory, p.match_id, work.content_hash, result)
            except LLMRateLimited:
                _pause_until = clock() + RATE_LIMIT_PAUSE_SECONDS
                logger.warning(
                    "LLM rate limited; pausing match scoring for %ds",
                    RATE_LIMIT_PAUSE_SECONDS,
                )
                return done
            except (MatchInvalid, LLMError) as exc:
                # class name and match id only: never the posting or the answer
                logger.warning("match %s scoring failed (%s)", p.match_id,
                               type(exc).__name__)
                _bump_attempts(session_factory, p.match_id, max_attempts, exc)
            except Exception as exc:  # noqa: BLE001 - isolate one row's failure
                logger.warning("match %s unexpected scoring failure (%s)", p.match_id,
                               type(exc).__name__)
                _bump_attempts(session_factory, p.match_id, max_attempts, exc)
            done += 1
    finally:
        _in_flight.difference_update(p.match_id for p in picked)
    return done
