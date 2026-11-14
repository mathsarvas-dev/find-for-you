"""Quality routes: labeled sample and precision@10 report.

Every query is scoped by the caller's user_id. `snippet` is plain text (the
client must render it as text, never as HTML).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.matching import (
    INT4_MAX,
    MSG_NO_PROFILE,
    MSG_NOT_VECTORIZED,
    model_version,
    usable_profile,
)
from rumo_core.db import get_session
from rumo_core.matching.quality import build_sample, evaluate, new_seed
from rumo_core.models import Company, Job, JobLabel

router = APIRouter(prefix="/quality", tags=["quality"])

SNIPPET_CHARS = 400
MSG_JOB_NOT_FOUND = "Vaga não encontrada."

Label = Literal["good", "bad"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SampleItem(_Out):
    job_id: int
    title: str
    company_name: str
    country: str | None
    url: str | None
    snippet: str
    label: Label | None


class QualitySampleOut(_Out):
    items: list[SampleItem]
    labeled_total: int


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label


class LabelOut(_Out):
    job_id: int
    label: Label


class MisrankedOut(_Out):
    job_id: int
    title: str
    label: Label
    position: int


class QualityReportOut(_Out):
    status: Literal["insuficiente", "dentro_da_meta", "abaixo_da_meta"]
    labeled: int
    good: int
    bad: int
    unranked: int
    missing: list[str]
    precision_at_10: float | None
    precision_vec_at_10: float | None
    target: float
    misranked: list[MisrankedOut]


def _owned_job_exists(session: Session, user_id: int, job_id: int) -> bool:
    if not 1 <= job_id <= INT4_MAX:  # outside the int4 id column: no such job
        return False
    return session.scalar(
        select(Job.id)
        .join(Company, Company.id == Job.company_id)
        .where(Job.id == job_id, Job.user_id == user_id, Company.user_id == user_id)
    ) is not None


def _require_job(session: Session, user_id: int, job_id: int) -> None:
    if not _owned_job_exists(session, user_id, job_id):
        raise HTTPException(404, MSG_JOB_NOT_FOUND)


def _sample_out(session: Session, user_id: int, ids: list[int]) -> QualitySampleOut:
    rows = {}
    if ids:
        rows = {
            r.id: r for r in session.execute(
                select(Job.id, Job.title, Job.country, Job.url, Company.name,
                       func.left(Job.description, SNIPPET_CHARS).label("snippet"))
                .join(Company, Company.id == Job.company_id)
                .where(Job.user_id == user_id, Company.user_id == user_id,
                       Job.id.in_(ids))
            )
        }
    labels = dict(session.execute(
        select(JobLabel.job_id, JobLabel.label).where(JobLabel.user_id == user_id)
    ).all())
    items = [
        SampleItem(
            job_id=r.id, title=r.title, company_name=r.name, country=r.country,
            url=r.url, snippet=r.snippet or "", label=labels.get(r.id),
        )
        for jid in ids if (r := rows.get(jid)) is not None
    ]
    return QualitySampleOut(items=items, labeled_total=len(labels))


def _require_vectorized(session: Session, user_id: int, mv: str) -> None:
    version, used = usable_profile(session, user_id, mv)
    if version is None:
        raise HTTPException(409, MSG_NO_PROFILE)
    if used == 0:
        raise HTTPException(409, MSG_NOT_VECTORIZED)


@router.get("/sample", response_model=QualitySampleOut)
def get_sample(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    _require_vectorized(session, user_id, mv)
    ids = build_sample(session, user_id, model_version=mv)
    return _sample_out(session, user_id, ids)


@router.post("/sample/new", response_model=QualitySampleOut)
def new_sample(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    _require_vectorized(session, user_id, mv)
    new_seed(session, user_id)
    ids = build_sample(session, user_id, model_version=mv)
    return _sample_out(session, user_id, ids)


@router.put("/labels/{job_id}", response_model=LabelOut)
def put_label(
    job_id: int,
    body: LabelIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    _require_job(session, user_id, job_id)
    stmt = insert(JobLabel).values(user_id=user_id, job_id=job_id, label=body.label)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_job_labels_user_job",
        set_={"label": stmt.excluded.label, "updated_at": func.now()},
    )
    try:
        session.execute(stmt)
        session.commit()
    except IntegrityError:  # job deleted between the check and the insert
        session.rollback()
        raise HTTPException(404, MSG_JOB_NOT_FOUND) from None
    return LabelOut(job_id=job_id, label=body.label)


@router.delete("/labels/{job_id}", status_code=204)
def delete_label(
    job_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    _require_job(session, user_id, job_id)
    session.execute(
        delete(JobLabel).where(JobLabel.user_id == user_id, JobLabel.job_id == job_id)
    )
    session.commit()
    return Response(status_code=204)


@router.get("/report", response_model=QualityReportOut)
def get_report(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    r = evaluate(session, user_id, model_version=mv)
    return QualityReportOut(
        status=r.status, labeled=r.labeled, good=r.good, bad=r.bad,
        unranked=r.unranked, missing=r.missing,
        precision_at_10=r.precision_at_10, precision_vec_at_10=r.precision_vec_at_10,
        target=r.target,
        misranked=[MisrankedOut(job_id=m.job_id, title=m.title, label=m.label,
                                position=m.position) for m in r.misranked],
    )
