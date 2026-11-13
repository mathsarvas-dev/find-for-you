"""Journey overview: one small, text-free summary of the user's progress.

Every query is scoped by the caller's user_id. Each section falls back to a
safe default when its data is missing or oddly shaped; the response carries
counters and flags only (never job text).
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.db import get_session
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.matching.ranking import usable_profile
from rumo_core.models import Company, InterviewSession, Job, Match
from rumo_core.profile.service import get_current_profile

router = APIRouter(tags=["overview"])

MAX_FIELD_LEN = 200


class ProfileOverview(BaseModel):
    confirmed: bool = False
    version: int | None = None
    vectorized: bool = False
    name: str | None = None
    email: str | None = None


class InterviewOverview(BaseModel):
    state: Literal["none", "in_progress", "done"] = "none"


class CompaniesOverview(BaseModel):
    total: int = 0
    active: int = 0
    auto: int = 0


class JobsOverview(BaseModel):
    open: int = 0


class MatchesOverview(BaseModel):
    scored: int = 0
    pending: int = 0


class OverviewOut(BaseModel):
    profile: ProfileOverview
    interview: InterviewOverview
    companies: CompaniesOverview
    jobs: JobsOverview
    matches: MatchesOverview


def _clean(value: object) -> str | None:
    """Trimmed text capped at MAX_FIELD_LEN; anything else/empty -> None."""
    if not isinstance(value, str):
        return None
    text = value.strip()[:MAX_FIELD_LEN].strip()
    return text or None


def _basics(data: object) -> tuple[str | None, str | None]:
    basics = data.get("basics") if isinstance(data, dict) else None
    if not isinstance(basics, dict):
        return None, None
    return _clean(basics.get("name")), _clean(basics.get("email"))


def _profile(session: Session, user_id: int, current) -> ProfileOverview:
    if current is None:
        return ProfileOverview()
    # Same notion of "vectorized" as the ranking: the current version has at
    # least one embedded chunk for the active embedding model.
    _, used = usable_profile(session, user_id, MODEL_VERSION)
    name, email = _basics(current.data)
    return ProfileOverview(
        confirmed=True, version=current.version, vectorized=used > 0,
        name=name, email=email,
    )


def _interview(session: Session, user_id: int, current) -> InterviewOverview:
    """Interview state for the journey.

    - no session at all for the user              -> "none"
    - most recent session of the CURRENT profile
      version is in state "done" (see
      InterviewSession: generating | working |
      awaiting_answer | done | error)             -> "done"
    - anything else (running, awaiting an answer,
      errored, or sessions only on older
      versions)                                   -> "in_progress"
    """
    has_any = session.scalar(
        select(InterviewSession.id)
        .where(InterviewSession.user_id == user_id).limit(1)
    )
    if has_any is None:
        return InterviewOverview(state="none")
    if current is None:
        return InterviewOverview(state="in_progress")
    latest = session.scalar(
        select(InterviewSession.state)
        .where(InterviewSession.user_id == user_id,
               InterviewSession.profile_version == current.version)
        .order_by(InterviewSession.id.desc()).limit(1)
    )
    return InterviewOverview(
        state="done" if latest == "done" else "in_progress"
    )


def _companies(session: Session, user_id: int) -> CompaniesOverview:
    total, active, auto = session.execute(
        select(
            func.count(Company.id),
            func.count(Company.id).filter(Company.active.is_(True)),
            func.count(Company.id).filter(Company.origin == "auto"),
        ).where(Company.user_id == user_id)
    ).one()
    return CompaniesOverview(total=total, active=active, auto=auto)


def _jobs(session: Session, user_id: int) -> JobsOverview:
    n = session.scalar(
        select(func.count(Job.id))
        .where(Job.user_id == user_id, Job.status == "open")
    )
    return JobsOverview(open=n or 0)


def _matches(session: Session, user_id: int, current) -> MatchesOverview:
    if current is None:
        return MatchesOverview()
    scored, pending = session.execute(
        select(
            func.count(Match.id).filter(
                Match.state == "ok", Match.content_hash == Job.content_hash
            ),
            func.count(Match.id).filter(Match.state == "pendente"),
        )
        .join(Job, Job.id == Match.job_id)
        .where(Match.user_id == user_id, Job.user_id == user_id,
               Match.profile_version == current.version)
    ).one()
    return MatchesOverview(scored=scored, pending=pending)


@router.get("/overview", response_model=OverviewOut)
def get_overview(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    current = get_current_profile(session, user_id)
    return OverviewOut(
        profile=_profile(session, user_id, current),
        interview=_interview(session, user_id, current),
        companies=_companies(session, user_id),
        jobs=_jobs(session, user_id),
        matches=_matches(session, user_id, current),
    )
