"""Adherence funnel routes: intake counts, the optional vector cut and its suggestion.

The cut only moves jobs between `full` and `gated`; stubs are never touched.
Counts cover open jobs only.
"""

import math

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.matching import model_version
from rumo_core.db import get_session
from rumo_core.matching.gate import apply_vec_gate
from rumo_core.matching.quality import suggest_floor
from rumo_core.models import Job, JobLabel, UserPreference

router = APIRouter(tags=["funnel"])

MSG_FLOOR_RANGE = "O corte deve estar entre 0 e 1."


class FunnelOut(BaseModel):
    full: int
    stub: int
    gated: int
    floor: float | None
    suggested_floor: float | None
    good_labels: int


class FloorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adherence_vec_floor: float | None

    @field_validator("adherence_vec_floor")
    @classmethod
    def _range(cls, v):
        if v is not None and not (math.isfinite(v) and 0.0 <= v <= 1.0):
            raise ValueError(MSG_FLOOR_RANGE)
        return v


def _funnel_out(session: Session, user_id: int, mv: str) -> FunnelOut:
    counts = dict(session.execute(
        select(Job.intake, func.count())
        .where(Job.user_id == user_id, Job.status == "open")
        .group_by(Job.intake)
    ).all())
    pref = session.get(UserPreference, user_id)
    good = session.scalar(
        select(func.count()).select_from(JobLabel)
        .where(JobLabel.user_id == user_id, JobLabel.label == "good")
    ) or 0
    return FunnelOut(
        full=counts.get("full", 0), stub=counts.get("stub", 0), gated=counts.get("gated", 0),
        floor=pref.adherence_vec_floor if pref is not None else None,
        suggested_floor=suggest_floor(session, user_id, model_version=mv),
        good_labels=good,
    )


@router.get("/funnel", response_model=FunnelOut)
def get_funnel(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    return _funnel_out(session, user_id, mv)


@router.put("/funnel", response_model=FunnelOut)
def put_funnel(
    body: FloorIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    pref = session.get(UserPreference, user_id)
    if pref is None:
        pref = UserPreference(user_id=user_id)
        session.add(pref)
    pref.adherence_vec_floor = body.adherence_vec_floor
    session.flush()
    apply_vec_gate(session, user_id, model_version=mv, floor=body.adherence_vec_floor)
    session.commit()
    return _funnel_out(session, user_id, mv)
