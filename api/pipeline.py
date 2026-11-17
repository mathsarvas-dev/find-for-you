"""Pipeline (marco 6): kanban stages, follow-up dates, notes and reports.

Built on the applications table: a saved job is an application in the
'saved' stage, so nothing is duplicated. Every query is scoped by user_id.
Nothing leaves the app: reminders are only shown here.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.applications import MSG_APP_NOT_FOUND, MSG_PROVISIONAL
from rumo_core.applications.stages import ANSWERED_STAGES, APPLIED_STAGES, move_stage
from rumo_core.db import get_session
from rumo_core.models import Application, Company, Job, Match
from rumo_core.profile.service import get_current_profile

router = APIRouter(tags=["pipeline"])

MAX_NOTES = 2000
Stage = Literal["saved", "preparing", "applied", "interview", "offer", "rejected", "no_response"]

# (key, lowest score) from the best band down
BANDS = (("80_100", 80), ("60_79", 60), ("0_59", 0))


class PipelineItem(BaseModel):
    id: int
    job_id: int
    job_title: str
    company_name: str
    stage: str
    state: str
    ai_score: int | None
    applied_at: str | None
    follow_up_at: date | None
    notes: str | None


class PipelineOut(BaseModel):
    items: list[PipelineItem]
    today: date


class PatchIn(BaseModel):
    stage: Stage | None = None
    follow_up_at: date | None = None
    notes: str | None = Field(default=None, max_length=MAX_NOTES)


class Group(BaseModel):
    key: str
    applied: int
    answered: int
    rate: float | None


class ReportOut(BaseModel):
    found: int
    saved: int
    applied: int
    answered: int
    response_rate: float | None
    by_country: list[Group]
    by_source: list[Group]
    by_score_band: list[Group]


def _score_map(session: Session, user_id: int) -> dict[int, int]:
    """job_id -> ai_score on the current profile version (scored matches only)."""
    current = get_current_profile(session, user_id)
    if current is None:
        return {}
    rows = session.execute(
        select(Match.job_id, Match.ai_score).where(
            Match.user_id == user_id, Match.profile_version == current.version,
            Match.state == "ok", Match.ai_score.is_not(None),
        )
    ).all()
    return {job_id: score for job_id, score in rows}


def _item(app: Application, title: str, company: str, score: int | None) -> PipelineItem:
    return PipelineItem(
        id=app.id, job_id=app.job_id, job_title=title, company_name=company,
        stage=app.stage, state=app.state, ai_score=score,
        applied_at=app.applied_at.isoformat() if app.applied_at else None,
        follow_up_at=app.follow_up_at, notes=app.notes,
    )


def _rows(session: Session, user_id: int):
    return session.execute(
        select(Application, Job, Company)
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
        .where(Application.user_id == user_id)
        .order_by(Application.updated_at.desc(), Application.id.desc())
    ).all()


@router.get("/pipeline", response_model=PipelineOut)
def get_pipeline(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    scores = _score_map(session, user_id)
    items = [
        _item(app, job.title, company.name, scores.get(job.id))
        for app, job, company in _rows(session, user_id)
    ]
    return PipelineOut(items=items, today=date.today())


@router.patch("/pipeline/{app_id}", response_model=PipelineItem)
def patch_pipeline(
    app_id: int,
    body: PatchIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    app = session.scalar(
        select(Application).where(Application.id == app_id, Application.user_id == user_id)
    )
    if app is None:
        raise HTTPException(404, MSG_APP_NOT_FOUND)
    sent = body.model_fields_set
    if "stage" in sent and body.stage is not None and body.stage != app.stage:
        if body.stage in APPLIED_STAGES and app.stage not in APPLIED_STAGES:
            current = get_current_profile(session, user_id)
            if current is not None and current.provisional:
                raise HTTPException(409, MSG_PROVISIONAL)
        move_stage(app, body.stage)
    if "follow_up_at" in sent:
        app.follow_up_at = body.follow_up_at
    if "notes" in sent:
        app.notes = (body.notes or "").strip() or None
    session.commit()
    row = session.execute(
        select(Job.title, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.id == app.job_id)
    ).one()
    return _item(app, row[0], row[1], _score_map(session, user_id).get(app.job_id))


def _groups(counts: dict[str, list[int]], order: list[str] | None = None) -> list[Group]:
    keys = order if order is not None else sorted(counts, key=lambda k: (-counts[k][0], k))
    return [
        Group(key=k, applied=counts[k][0], answered=counts[k][1],
              rate=counts[k][1] / counts[k][0])
        for k in keys if k in counts
    ]


@router.get("/pipeline/report", response_model=ReportOut)
def pipeline_report(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    found = session.query(Job).filter(
        Job.user_id == user_id, Job.intake != "stub", Job.status != "duplicate"
    ).count()
    scores = _score_map(session, user_id)
    saved = applied = answered = 0
    country: dict[str, list[int]] = {}
    source: dict[str, list[int]] = {}
    band: dict[str, list[int]] = {}
    for app, job, company in _rows(session, user_id):
        if app.stage not in APPLIED_STAGES:
            saved += 1
            continue
        got = 1 if app.stage in ANSWERED_STAGES else 0
        applied += 1
        answered += got
        score = scores.get(job.id)
        band_key = "none" if score is None else next(k for k, low in BANDS if score >= low)
        for table, key in ((country, job.country or "unknown"), (source, company.source), (band, band_key)):
            cell = table.setdefault(key, [0, 0])
            cell[0] += 1
            cell[1] += got
    return ReportOut(
        found=found, saved=saved, applied=applied, answered=answered,
        response_rate=answered / applied if applied else None,
        by_country=_groups(country), by_source=_groups(source),
        by_score_band=_groups(band, [k for k, _ in BANDS] + ["none"]),
    )
