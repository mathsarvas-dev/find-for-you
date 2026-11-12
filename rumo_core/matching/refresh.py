"""Mark the best candidate jobs of the user's current profile for AI scoring.

`mark_pending` creates `pendente` Match rows for the top `n` jobs by
vec_score (same filters as the ranking) for the current profile version.
An `ok` row whose job is unchanged (job content_hash == Match.content_hash,
the hash at scoring time) is kept; an `ok` row whose job changed and any
`erro` row go back to `pendente` with attempts reset and the old AI result
cleared. `pendente` rows are left alone, so calling it again is a no-op.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.ranking import top_candidate_ids
from rumo_core.models import Job, Match
from rumo_core.profile.service import get_current_profile


@dataclass(frozen=True)
class RefreshResult:
    requested: int
    created: int
    reset: int
    already_ok: int
    profile_version: int | None


def _reset(m: Match, vec_score: float, content_hash: str) -> None:
    m.state = "pendente"
    m.attempts = 0
    m.error = None
    m.vec_score = vec_score
    m.content_hash = content_hash
    m.ai_score = None
    m.rationale = None
    m.strengths = None
    m.gaps = None
    m.scored_at = None


def mark_pending(session: Session, user_id: int, filters: SearchFilters, *,
                 model_version: str, n: int) -> RefreshResult:
    """Create/reset pendente rows for the top `n` candidates; commits."""
    profile = get_current_profile(session, user_id)
    if profile is None:
        return RefreshResult(0, 0, 0, 0, None)
    version = profile.version
    candidates = top_candidate_ids(session, user_id, filters,
                                   model_version=model_version, n=n)
    if not candidates:
        return RefreshResult(0, 0, 0, 0, version)
    ids = [job_id for job_id, _ in candidates]
    hashes = dict(session.execute(
        select(Job.id, Job.content_hash).where(Job.user_id == user_id, Job.id.in_(ids))
    ).all())
    candidates = [(j, v) for j, v in candidates if j in hashes]

    # concurrent refreshes / a worker write: insert-if-absent, then lock
    created = 0
    for job_id, vec in candidates:
        new_id = session.execute(
            insert(Match)
            .values(user_id=user_id, job_id=job_id, profile_version=version,
                    vec_score=vec, content_hash=hashes[job_id], state="pendente")
            .on_conflict_do_nothing(constraint="uq_matches_user_job_version")
            .returning(Match.id)
        ).scalar_one_or_none()
        created += new_id is not None
    existing = {
        m.job_id: m
        for m in session.execute(
            select(Match)
            .where(Match.user_id == user_id, Match.profile_version == version,
                   Match.job_id.in_([j for j, _ in candidates]))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    }
    reset = already_ok = 0
    for job_id, vec in candidates:
        m = existing[job_id]
        if m.state == "erro" or (m.state == "ok" and m.content_hash != hashes[job_id]):
            _reset(m, vec, hashes[job_id])
            reset += 1
        elif m.state == "ok":
            already_ok += 1
    session.commit()
    return RefreshResult(len(candidates), created, reset, already_ok, version)
