"""Companies and jobs routes (collection config + read-only job browsing).

Only POST /companies touches the network (board validation). Salary amounts are
serialised as JSON numbers (float), converted explicitly from Decimal.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from api.deps import current_user_id
from rumo_core.db import get_session
from rumo_core.models import Company, Job
from rumo_core.sources.country import norm_country
from rumo_core.sources import (
    Source,
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
    get_source,
)

router = APIRouter(tags=["sources"])

FETCH_TIMEOUT_SECONDS = 15.0


def source_getter() -> Callable[[str], Source]:
    """Dependency returning the source factory (overridden in tests)."""
    return get_source


class CompanyIn(BaseModel):
    source: Literal["greenhouse"]
    identifier: str = Field(max_length=500)
    name: str | None = Field(default=None, max_length=200)
    country: str | None = None

    @field_validator("country")
    @classmethod
    def _country(cls, v):
        return norm_country(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Nome não pode ser vazio.")
        return v.strip() if v is not None else None


class CompanyPatch(BaseModel):
    active: bool | None = None
    name: str | None = Field(default=None, max_length=200)
    country: str | None = None

    @field_validator("country")
    @classmethod
    def _country(cls, v):
        return norm_country(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Nome não pode ser vazio.")
        return v.strip() if v is not None else None


class CompanyOut(BaseModel):
    id: int
    source: str
    identifier: str
    name: str
    country: str | None
    active: bool
    last_run_at: datetime | None
    last_success_at: datetime | None
    next_run_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    open_jobs: int


class JobItem(BaseModel):
    id: int
    company_id: int
    company_name: str
    title: str
    url: str
    location_text: str
    country: str | None
    city: str | None
    work_model: str
    seniority: str
    language: str
    department: str | None
    salary_known: bool
    status: str
    first_seen_at: datetime
    posted_at: datetime | None


class JobList(BaseModel):
    total: int
    items: list[JobItem]


class JobDetail(JobItem):
    description: str
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    last_seen_at: datetime
    closed_at: datetime | None


def _company_out(c: Company) -> CompanyOut:
    return CompanyOut(
        id=c.id, source=c.source, identifier=c.identifier, name=c.name,
        country=c.country, active=c.active, last_run_at=c.last_run_at,
        last_success_at=c.last_success_at, next_run_at=c.next_run_at,
        last_error=c.last_error, consecutive_failures=c.consecutive_failures,
        open_jobs=c.open_jobs,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_company(session: Session, user_id: int, company_id: int) -> Company:
    row = session.scalars(
        select(Company).where(Company.id == company_id, Company.user_id == user_id)
    ).first()
    if row is None:
        raise HTTPException(404, "Empresa não encontrada.")
    return row


def _create_company(
    session: Session, user_id: int, source: str, identifier: str,
    name: str | None, country: str | None,
) -> CompanyOut:
    dup = session.scalars(
        select(Company.id).where(
            Company.user_id == user_id, Company.source == source,
            Company.identifier == identifier,
        )
    ).first()
    if dup is not None:
        raise HTTPException(409, "Essa empresa já está cadastrada.")
    row = Company(
        user_id=user_id, source=source, identifier=identifier,
        name=name or identifier, country=country, active=True, next_run_at=_now(),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Essa empresa já está cadastrada.") from None
    session.refresh(row)
    return _company_out(row)


@router.post("/companies", status_code=201, response_model=CompanyOut)
async def add_company(
    body: CompanyIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    get_src: Callable[[str], Source] = Depends(source_getter),
):
    source = get_src(body.source)
    try:
        identifier = source.normalize_identifier(body.identifier)
    except ValueError:
        raise HTTPException(422, "Token do quadro inválido.") from None
    # Network validation first; no DB transaction is open during the await.
    try:
        await asyncio.wait_for(source.fetch_jobs(identifier), timeout=FETCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(503, "A fonte demorou demais para responder.") from None
    except SourceNotFound:
        raise HTTPException(422, "Quadro não encontrado no Greenhouse.") from None
    except SourceUnavailable:
        raise HTTPException(503, "Fonte indisponível no momento.") from None
    except SourceBadResponse:
        raise HTTPException(502, "Resposta inesperada da fonte.") from None
    return await run_in_threadpool(
        _create_company, session, user_id, body.source, identifier, body.name, body.country
    )


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    rows = session.scalars(
        select(Company).where(Company.user_id == user_id).order_by(Company.name, Company.id)
    ).all()
    return [_company_out(c) for c in rows]


@router.patch("/companies/{company_id}", response_model=CompanyOut)
def patch_company(
    company_id: int,
    body: CompanyPatch,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = _get_company(session, user_id, company_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        row.name = body.name
    if "country" in fields:
        row.country = body.country
    if "active" in fields and body.active is not None:
        if body.active and not row.active:
            row.next_run_at = _now()
        row.active = body.active
    session.commit()
    session.refresh(row)
    return _company_out(row)


@router.post("/companies/{company_id}/collect-now", response_model=CompanyOut)
def collect_now(
    company_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = _get_company(session, user_id, company_id)
    if not row.active:
        raise HTTPException(409, "Empresa inativa.")
    row.next_run_at = _now()
    session.commit()
    session.refresh(row)
    return _company_out(row)


_ITEM_COLS = (
    Job.id, Job.company_id, Company.name.label("company_name"), Job.title, Job.url,
    Job.location_text, Job.country, Job.city, Job.work_model, Job.seniority,
    Job.language, Job.department, Job.salary_known, Job.status, Job.first_seen_at,
    Job.posted_at,
)


def _item_fields(r) -> dict:
    return {
        k: getattr(r, k) for k in (
            "id", "company_id", "company_name", "title", "url", "location_text",
            "country", "city", "work_model", "seniority", "language", "department",
            "salary_known", "status", "first_seen_at", "posted_at",
        )
    }


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/jobs", response_model=JobList)
def list_jobs(
    status: Literal["open", "closed", "duplicate", "all"] = "open",
    company_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=100),
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    conds = [Job.user_id == user_id, Company.user_id == user_id]
    if status != "all":
        conds.append(Job.status == status)
    if company_id is not None:
        conds.append(Job.company_id == company_id)
    if q:
        conds.append(Job.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    base = select(*_ITEM_COLS).join(Company, Company.id == Job.company_id).where(*conds)
    total = session.scalar(
        select(func.count()).select_from(Job).join(Company, Company.id == Job.company_id)
        .where(*conds)
    )
    rows = session.execute(
        base.order_by(Job.first_seen_at.desc(), Job.id.desc()).limit(limit).offset(offset)
    ).all()
    return JobList(total=total or 0, items=[JobItem(**_item_fields(r)) for r in rows])


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


@router.get("/jobs/{job_id}", response_model=JobDetail)
def job_detail(
    job_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    r = session.execute(
        select(
            *_ITEM_COLS, Job.description, Job.salary_min, Job.salary_max,
            Job.salary_currency, Job.salary_period, Job.last_seen_at, Job.closed_at,
        )
        .join(Company, Company.id == Job.company_id)
        .where(Job.id == job_id, Job.user_id == user_id, Company.user_id == user_id)
    ).first()
    if r is None:
        raise HTTPException(404, "Vaga não encontrada.")
    return JobDetail(
        **_item_fields(r), description=r.description,
        salary_min=_f(r.salary_min), salary_max=_f(r.salary_max),
        salary_currency=r.salary_currency, salary_period=r.salary_period,
        last_seen_at=r.last_seen_at, closed_at=r.closed_at,
    )
