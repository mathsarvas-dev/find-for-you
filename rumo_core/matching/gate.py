"""Optional vector floor: jobs under the user's cut are `gated` (kept, not filled or scored).

`intake` moves only between `full` and `gated`; stubs are never touched here.
The cut is applied to the vec_score the ranking already computes, so it
costs no LLM calls. Funnel companies only (`collect_mode='all'` is exempt).
"""

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.ranking import (
    top_candidate_ids,
    usable_profile,
    usable_query_embedding,
)
from rumo_core.models import Company, Job

_NO_LIMIT = 2**31 - 1


@dataclass(frozen=True)
class GateResult:
    gated: int
    restored: int


def _restore_gated(session: Session, user_id: int, job_ids: Collection[int] | None) -> int:
    stmt = (
        select(Job)
        .join(Company, Company.id == Job.company_id)
        .where(
            Job.user_id == user_id, Job.status == "open", Job.intake == "gated",
            Company.collect_mode == "funnel",
        )
        .options(defer(Job.embedding), defer(Job.raw), defer(Job.description))
    )
    if job_ids is not None:
        stmt = stmt.where(Job.id.in_(list(job_ids)))
    restored = 0
    for job in session.scalars(stmt):
        job.intake = "full"
        job.llm_filled_at = None
        job.fill_attempts = 0
        restored += 1
    session.flush()
    return restored


def apply_vec_gate(
    session: Session, user_id: int, *, model_version: str, floor: float | None,
    job_ids: Collection[int] | None = None,
) -> GateResult:
    """Move jobs across the cut. Does not commit.

    A full job scoring below `floor` becomes gated. A gated job scoring at or
    above it (or every gated job when `floor` is None) becomes full again and
    goes back to the fill step (llm_filled_at and fill_attempts reset).
    """
    if job_ids is not None and not job_ids:
        return GateResult(0, 0)
    if floor is None:
        # Cut switched off: every gated job returns, with or without a profile.
        return GateResult(0, _restore_gated(session, user_id, job_ids))
    version, used = usable_profile(session, user_id, model_version)
    if version is None or used == 0:
        return GateResult(0, 0)
    scores = dict(top_candidate_ids(
        session, user_id, SearchFilters(), model_version=model_version, n=_NO_LIMIT,
        status="open", intakes=("full", "gated"), only_ids=job_ids,
        query_embedding=usable_query_embedding(session, user_id, model_version),
    ))
    stmt = (
        select(Job)
        .join(Company, Company.id == Job.company_id)
        .where(
            Job.user_id == user_id, Job.status == "open",
            Job.intake.in_(("full", "gated")), Company.collect_mode == "funnel",
        )
        .options(defer(Job.embedding), defer(Job.raw), defer(Job.description))
    )
    if job_ids is not None:
        stmt = stmt.where(Job.id.in_(list(job_ids)))
    gated = restored = 0
    for job in session.scalars(stmt):
        score = scores.get(job.id)
        if score is None:
            continue
        if job.intake == "full" and floor is not None and score < floor:
            job.intake = "gated"
            gated += 1
        elif job.intake == "gated" and (floor is None or score >= floor):
            job.intake = "full"
            job.llm_filled_at = None
            job.fill_attempts = 0
            restored += 1
    session.flush()
    return GateResult(gated, restored)
