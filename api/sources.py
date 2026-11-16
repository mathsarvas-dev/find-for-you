"""Companies and jobs routes (collection config + read-only job browsing).

Only POST /companies touches the network (board validation). Salary amounts are
serialised as JSON numbers (float), converted explicitly from Decimal.
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
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
    ats_from_url,
    get_source,
    shared_client,
)
from rumo_core.sources.base import ATS_SOURCES
from rumo_core.sources.careers import MSG_NO_STRUCTURED
from rumo_core.sources.feeds import feed_attribution
from rumo_core.sources.http import SafeClient, UnsafeURL
from rumo_core.sources.link_import import (
    NeedsPastedText,
    pasted_raw,
    read_link,
    save_imported_job,
)
from rumo_core.sources.portals.gate import Verdict, check, portal_for_url
from rumo_core.sources.portals.state import load_states

router = APIRouter(tags=["sources"])

FETCH_TIMEOUT_SECONDS = 15.0
IMPORT_FETCH_TIMEOUT_SECONDS = 25.0
IMPORT_LIMIT = 20  # imports per user per window (per process)
IMPORT_WINDOW_SECONDS = 3600.0
_IMPORTS: dict[int, deque[float]] = {}
MSG_NEEDS_TEXT = "Não consegui ler esta vaga automaticamente. Cole o título e a descrição."
MSG_NEED_SOURCE = "Informe a fonte ou cole a URL da página de carreiras."
MSG_UNKNOWN_SOURCE = "Fonte desconhecida."
MSG_UNSAFE_URL = "Endereço não permitido."
SITE = "site"
# company origins shown in the companies list (feed/link companies are synthetic)
MANUAL_ORIGINS = ("manual", "auto")
SOURCE_LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters", "breezy": "Breezy", "recruitee": "Recruitee",
    "personio": "Personio", "teamtailor": "Teamtailor", "bamboohr": "BambooHR",
    "pinpoint": "Pinpoint", SITE: "Página de carreira",
}


def source_getter() -> Callable[[str], Source]:
    """Dependency returning the source factory (overridden in tests)."""
    return get_source


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _is_http_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


class CompanyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(default=None, max_length=32)
    identifier: str = Field(max_length=500)
    name: str | None = Field(default=None, max_length=200)
    country: str | None = None
    collect_mode: Literal["funnel", "all"] = "funnel"

    @field_validator("source")
    @classmethod
    def _source(cls, v):
        if v is not None and v not in ATS_SOURCES and v != SITE:
            raise ValueError(MSG_UNKNOWN_SOURCE)
        return v

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
    collect_mode: Literal["funnel", "all"] | None = None

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
    origin: str
    collect_mode: str
    # open jobs stored as stubs: they fail the user's filters (not counted above)
    stub_jobs: int = 0


class Attribution(BaseModel):
    label: str
    url: str


class JobItem(BaseModel):
    id: int
    company_id: int
    company_name: str
    origin: str = "ats"
    source: str = ""
    attribution: Attribution | None = None
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


def _company_out(c: Company, stub_jobs: int = 0) -> CompanyOut:
    return CompanyOut(
        id=c.id, source=c.source, identifier=c.identifier, name=c.name,
        country=c.country, active=c.active, last_run_at=c.last_run_at,
        last_success_at=c.last_success_at, next_run_at=c.next_run_at,
        last_error=c.last_error, consecutive_failures=c.consecutive_failures,
        open_jobs=c.open_jobs, origin=c.origin, collect_mode=c.collect_mode,
        stub_jobs=stub_jobs,
    )


def _stub_counts(session: Session, user_id: int, company_id: int | None = None) -> dict[int, int]:
    """Open stub jobs per company (the jobs that fail the user's filters)."""
    conds = [Job.user_id == user_id, Job.status == "open", Job.intake == "stub"]
    if company_id is not None:
        conds.append(Job.company_id == company_id)
    rows = session.execute(
        select(Job.company_id, func.count(Job.id)).where(*conds).group_by(Job.company_id)
    ).all()
    return dict(rows)


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
    name: str | None, country: str | None, collect_mode: str = "funnel",
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
        collect_mode=collect_mode,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Essa empresa já está cadastrada.") from None
    session.refresh(row)
    return _company_out(row)


def _site_verdict(session: Session, user_id: int, portal_id: str, url: str) -> Verdict:
    """Automatic-collection verdict for a career page hosted on a known portal."""
    state = load_states(session, user_id).get(portal_id)
    return check(portal_id, url, state=state)


async def _add_site(
    session: Session, user_id: int, body: CompanyIn, url: str, get_src: Callable[[str], Source],
) -> CompanyOut:
    """A careers URL: connect the ATS it embeds, or follow its JobPosting data."""
    site = get_src(SITE)
    try:
        identifier = site.normalize_identifier(url)
    except (ValueError, UnsafeURL):
        raise HTTPException(422, MSG_UNSAFE_URL) from None
    portal = portal_for_url(identifier)
    if portal is not None:
        verdict = await run_in_threadpool(_site_verdict, session, user_id, portal.id, identifier)
        if not verdict.allowed:
            raise HTTPException(409, verdict.message)
    try:
        found = await asyncio.wait_for(
            site.probe(identifier), timeout=IMPORT_FETCH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise HTTPException(503, "A fonte demorou demais para responder.") from None
    except UnsafeURL:
        raise HTTPException(422, MSG_UNSAFE_URL) from None
    except SourceUnavailable:
        raise HTTPException(503, "Fonte indisponível no momento.") from None
    except SourceBadResponse:
        raise HTTPException(502, "Resposta inesperada da fonte.") from None
    if found.kind == "none":
        raise HTTPException(422, MSG_NO_STRUCTURED)
    if found.kind == "ats":
        try:
            token = get_src(found.source).normalize_identifier(found.identifier)
        except ValueError:
            raise HTTPException(422, "Token do quadro inválido.") from None
        return await run_in_threadpool(
            _create_company, session, user_id, found.source, token,
            body.name, body.country, body.collect_mode,
        )
    name = body.name or urlsplit(identifier).hostname or identifier
    return await run_in_threadpool(
        _create_company, session, user_id, SITE, identifier,
        name, body.country, body.collect_mode,
    )


@router.post("/companies", status_code=201, response_model=CompanyOut)
async def add_company(
    body: CompanyIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    get_src: Callable[[str], Source] = Depends(source_getter),
):
    """Add a company by board token/URL of an ATS, or by a careers page URL.

    Without `source`, an ATS board URL is recognised by its host; any other
    http(s) URL is read as a careers page (`site`).
    """
    raw = body.identifier.strip()
    source = body.source
    if source is None:
        hit = ats_from_url(raw)
        if hit is not None:
            source = hit[0]
        elif _is_http_url(raw):
            source = SITE
        else:
            raise HTTPException(422, MSG_NEED_SOURCE)
    if source == SITE:
        return await _add_site(session, user_id, body, raw, get_src)
    connector = get_src(source)
    try:
        identifier = connector.normalize_identifier(raw)
    except ValueError:
        raise HTTPException(422, "Token do quadro inválido.") from None
    # Network validation first; no DB transaction is open during the await.
    try:
        await asyncio.wait_for(connector.fetch_jobs(identifier), timeout=FETCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(503, "A fonte demorou demais para responder.") from None
    except SourceNotFound:
        raise HTTPException(
            422, f"Quadro não encontrado no {source_label(source)}."
        ) from None
    except SourceUnavailable:
        raise HTTPException(503, "Fonte indisponível no momento.") from None
    except SourceBadResponse:
        raise HTTPException(502, "Resposta inesperada da fonte.") from None
    return await run_in_threadpool(
        _create_company, session, user_id, source, identifier, body.name, body.country,
        body.collect_mode,
    )


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(
    origin: Literal["manual", "auto", "feed", "link"] | None = None,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    conds = [Company.user_id == user_id]
    if origin is not None:
        conds.append(Company.origin == origin)
    else:
        conds.append(Company.origin.in_(MANUAL_ORIGINS))
    rows = session.scalars(
        select(Company).where(*conds).order_by(Company.name, Company.id)
    ).all()
    stubs = _stub_counts(session, user_id)
    return [_company_out(c, stubs.get(c.id, 0)) for c in rows]


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
    if "collect_mode" in fields and body.collect_mode is not None:
        if body.collect_mode != row.collect_mode:
            row.collect_mode = body.collect_mode
            row.next_run_at = _now()
    session.commit()
    session.refresh(row)
    return _company_out(row, _stub_counts(session, user_id, row.id).get(row.id, 0))


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
    return _company_out(row, _stub_counts(session, user_id, row.id).get(row.id, 0))


def safe_client() -> SafeClient:
    """Dependency: the process-wide SafeClient (overridden in tests)."""
    return shared_client()


class ImportLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)


def _import_allowed(user_id: int, now: float) -> bool:
    window = _IMPORTS.setdefault(user_id, deque())
    while window and now - window[0] >= IMPORT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= IMPORT_LIMIT:
        return False
    window.append(now)
    return True


def _save_import(session: Session, user_id: int, url: str, raw) -> tuple[int, bool]:
    try:
        job, created = save_imported_job(session, user_id, url, raw, now=_now())
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Essa vaga está sendo importada agora. Tente de novo.") from None
    return job.id, created


@router.post("/jobs/import-link")
async def import_link(
    body: ImportLinkIn,
    response: Response,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    client: SafeClient = Depends(safe_client),
):
    """Import one job by link. Reads the page's JobPosting, or uses the pasted text.

    201 when the job is new, 200 when it was already imported. The URL is never logged.
    """
    if (body.title is None) != (body.description is None):
        raise HTTPException(422, "Informe o título e a descrição juntos.")
    if not _import_allowed(user_id, time.monotonic()):
        raise HTTPException(429, "Muitas importações em pouco tempo. Tente de novo mais tarde.")
    try:
        if body.title is not None:
            raw = pasted_raw(body.url, body.title, body.description or "")
        else:
            raw = await asyncio.wait_for(
                read_link(client, body.url), IMPORT_FETCH_TIMEOUT_SECONDS
            )
    except asyncio.TimeoutError:
        raise HTTPException(504, "A página demorou demais para responder.") from None
    except UnsafeURL:
        raise HTTPException(422, "Endereço não permitido.") from None
    except NeedsPastedText as exc:
        raise HTTPException(422, {
            "code": "needs_text", "reason": exc.reason, "message": MSG_NEEDS_TEXT,
        }) from None
    except SourceUnavailable:
        raise HTTPException(503, "Fonte indisponível no momento.") from None
    job_id, created = await run_in_threadpool(_save_import, session, user_id, body.url, raw)
    response.status_code = 201 if created else 200
    return {"job_id": job_id, "created": created}


_ITEM_COLS = (
    Job.id, Job.company_id, Company.name.label("company_name"), Job.title, Job.url,
    Job.location_text, Job.country, Job.city, Job.work_model, Job.seniority,
    Job.language, Job.department, Job.salary_known, Job.status, Job.first_seen_at,
    Job.posted_at, Job.origin, Company.source.label("source"),
)


def _attribution_for(origin: str, source: str) -> Attribution | None:
    """Credit line of a feed job (the aggregator must be credited where it is shown)."""
    if origin != "feed":
        return None
    found = feed_attribution(source)
    return Attribution(label=found.label, url=found.url) if found is not None else None


def _item_fields(r) -> dict:
    fields = {
        k: getattr(r, k) for k in (
            "id", "company_id", "company_name", "title", "url", "location_text",
            "country", "city", "work_model", "seniority", "language", "department",
            "salary_known", "status", "first_seen_at", "posted_at", "origin", "source",
        )
    }
    fields["attribution"] = _attribution_for(r.origin, r.source)
    return fields


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
    # stubs (filtered out at intake) are never listed
    conds = [Job.user_id == user_id, Company.user_id == user_id, Job.intake != "stub"]
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
        .where(Job.id == job_id, Job.user_id == user_id, Company.user_id == user_id,
               Job.intake != "stub")
    ).first()
    if r is None:
        raise HTTPException(404, "Vaga não encontrada.")
    return JobDetail(
        **_item_fields(r), description=r.description,
        salary_min=_f(r.salary_min), salary_max=_f(r.salary_max),
        salary_currency=r.salary_currency, salary_period=r.salary_period,
        last_seen_at=r.last_seen_at, closed_at=r.closed_at,
    )
