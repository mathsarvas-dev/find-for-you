"""Vector score and ranking of a user's jobs against their current profile.

vec_score = mean of the 3 largest normalized cosine similarities
(1 - cosine_distance / 2, in [0, 1]) between the job embedding and the
embedded chunks of the user's current profile version for `model_version`.
Computed entirely in SQL through a LATERAL top-3 subquery per job.
"""

from dataclasses import dataclass

from sqlalchemy import Select, and_, func, select, true
from sqlalchemy.orm import Session

from rumo_core.matching.filters import SearchFilters, apply_filters
from rumo_core.models import Job, Match, ProfileChunk
from rumo_core.profile.service import get_current_profile

STATUSES = {"open", "closed", "all"}
TOP_K = 3


@dataclass
class RankedJob:
    job: Job
    vec_score: float
    match: Match | None


@dataclass
class RankPage:
    total: int
    items: list[RankedJob]
    profile_version: int | None
    vectorized: bool
    missing_embeddings: int
    chunks_used: int


def _status_cond(status: str):
    if status not in STATUSES:
        raise ValueError(f"status inválido: {status!r}")
    if status == "all":
        # 'duplicate' rows are never shown.
        return Job.status.in_(("open", "closed"))
    return Job.status == status


def _chunk_conds(user_id: int, version: int, model_version: str):
    return (
        ProfileChunk.user_id == user_id,
        ProfileChunk.profile_version == version,
        ProfileChunk.embedding.is_not(None),
        ProfileChunk.model_version == model_version,
    )


def _base_jobs(user_id: int, filters: SearchFilters, status: str, stmt: Select) -> Select:
    stmt = stmt.where(Job.user_id == user_id, _status_cond(status))
    return apply_filters(stmt, filters)


def _scores(user_id: int, version: int, filters: SearchFilters, *,
            model_version: str, status: str):
    """Subquery (job_id, vec_score) for the filtered, embedded jobs."""
    dist = ProfileChunk.embedding.cosine_distance(Job.embedding)
    top = (
        select((1 - dist / 2).label("sim"))
        .where(*_chunk_conds(user_id, version, model_version))
        .order_by(dist)
        .limit(TOP_K)
        .lateral("top_chunks")
    )
    stmt = (
        select(Job.id.label("job_id"), func.avg(top.c.sim).label("vec_score"))
        .select_from(Job)
        .join(top, true())
        .where(Job.embedding.is_not(None), Job.model_version == model_version)
        .group_by(Job.id)
    )
    return _base_jobs(user_id, filters, status, stmt).subquery("scores")


def _missing(session: Session, user_id: int, filters: SearchFilters, *,
             model_version: str, status: str) -> int:
    stmt = select(func.count(Job.id)).where(
        (Job.embedding.is_(None))
        | (Job.model_version.is_(None))
        | (Job.model_version != model_version)
    )
    return session.scalar(_base_jobs(user_id, filters, status, stmt)) or 0


def _usable(session: Session, user_id: int, model_version: str):
    profile = get_current_profile(session, user_id)
    if profile is None:
        return None, 0
    used = session.scalar(
        select(func.count(ProfileChunk.id)).where(
            *_chunk_conds(user_id, profile.version, model_version)
        )
    ) or 0
    return profile.version, used


def rank_jobs(session: Session, user_id: int, filters: SearchFilters, *,
              model_version: str, status: str = "open",
              limit: int = 50, offset: int = 0) -> RankPage:
    _status_cond(status)
    missing = _missing(session, user_id, filters,
                       model_version=model_version, status=status)
    version, used = _usable(session, user_id, model_version)
    if version is None or used == 0:
        return RankPage(total=0, items=[], profile_version=version,
                        vectorized=False, missing_embeddings=missing,
                        chunks_used=used)

    scores = _scores(user_id, version, filters,
                     model_version=model_version, status=status)
    total = session.scalar(select(func.count()).select_from(scores)) or 0
    rows = session.execute(
        select(Job, scores.c.vec_score, Match)
        .join(scores, scores.c.job_id == Job.id)
        .outerjoin(Match, and_(
            Match.job_id == Job.id,
            Match.user_id == user_id,
            Match.profile_version == version,
        ))
        .where(Job.user_id == user_id)
        .order_by(
            Match.ai_score.desc().nulls_last(),
            scores.c.vec_score.desc(),
            Job.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    items = [RankedJob(job=j, vec_score=float(v), match=m) for j, v, m in rows]
    return RankPage(total=total, items=items, profile_version=version,
                    vectorized=True, missing_embeddings=missing,
                    chunks_used=used)


def top_candidate_ids(session: Session, user_id: int, filters: SearchFilters, *,
                      model_version: str, n: int,
                      status: str = "open") -> list[tuple[int, float]]:
    """The `n` best (job_id, vec_score) by vec_score only, same filters."""
    _status_cond(status)
    version, used = _usable(session, user_id, model_version)
    if version is None or used == 0 or n <= 0:
        return []
    scores = _scores(user_id, version, filters,
                     model_version=model_version, status=status)
    rows = session.execute(
        select(scores.c.job_id, scores.c.vec_score)
        .order_by(scores.c.vec_score.desc(), scores.c.job_id.asc())
        .limit(n)
    ).all()
    return [(jid, float(v)) for jid, v in rows]
