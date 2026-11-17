"""Highlights for Início (R9): new jobs with a high score and follow-ups due.

Only shown inside the app: no e-mail, push or any other notification.
Every query is scoped by user_id.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.applications.stages import CLOSED_STAGES
from rumo_core.db import get_session
from rumo_core.models import Application, Company, Job, Match
from rumo_core.profile.service import get_current_profile

router = APIRouter(tags=["highlights"])

MIN_SCORE = 80
WINDOW_DAYS = 7
MAX_ITEMS = 5


class HighlightItem(BaseModel):
    job_id: int
    job_title: str
    company_name: str
    ai_score: int


class HighlightsOut(BaseModel):
    min_score: int
    days: int
    new_high_count: int
    items: list[HighlightItem]
    follow_ups_due: int


@router.get("/highlights", response_model=HighlightsOut)
def get_highlights(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    count = 0
    items: list[HighlightItem] = []
    current = get_current_profile(session, user_id)
    if current is not None:
        since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
        saved = select(Application.job_id).where(Application.user_id == user_id)
        base = (
            select(Job, Company.name, Match.ai_score)
            .join(Match, Match.job_id == Job.id)
            .join(Company, Company.id == Job.company_id)
            .where(
                Job.user_id == user_id, Company.user_id == user_id, Match.user_id == user_id,
                Job.status == "open", Job.intake != "stub", Job.first_seen_at >= since,
                Match.profile_version == current.version, Match.state == "ok",
                Match.content_hash == Job.content_hash, Match.ai_score >= MIN_SCORE,
                Job.id.not_in(saved),
            )
        )
        count = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = session.execute(
            base.order_by(Match.ai_score.desc(), Job.first_seen_at.desc(), Job.id).limit(MAX_ITEMS)
        ).all()
        items = [
            HighlightItem(job_id=j.id, job_title=j.title, company_name=name, ai_score=score)
            for j, name, score in rows
        ]
    due = session.scalar(
        select(func.count(Application.id)).where(
            Application.user_id == user_id,
            Application.follow_up_at.is_not(None),
            Application.follow_up_at <= date.today(),
            Application.stage.notin_(sorted(CLOSED_STAGES)),
        )
    )
    return HighlightsOut(
        min_score=MIN_SCORE, days=WINDOW_DAYS, new_high_count=count, items=items,
        follow_ups_due=due or 0,
    )
