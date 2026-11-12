"""Search profile and job matching routes.

Every query is scoped by the caller's user_id. The embedder's model version
comes from a constant (no torch import here); AI scores are produced by the
worker only — POST /matches/refresh just marks candidates as `pendente`.
"""

import threading
import time
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.sources import JobDetail, JobItem, _f, _ITEM_COLS, _item_fields
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.ranking import (
    job_vec_score, rank_jobs, usable_profile,
)
from rumo_core.matching.refresh import mark_pending
from rumo_core.models import Company, Job, Match, SearchProfile

router = APIRouter(tags=["matching"])

DEFAULT_PROFILE_NAME = "default"
REFRESH_INTERVAL_SECONDS = 10.0
INT4_MAX = 2**31 - 1

MSG_NO_PROFILE = "Confirme seu perfil antes de avaliar vagas."
MSG_NOT_VECTORIZED = (
    "Seu perfil ainda está sendo processado. Tente novamente em instantes."
)
MSG_RATE_LIMITED = "Aguarde alguns segundos antes de avaliar as vagas novamente."
MSG_BAD_SAVED_FILTERS = "Os filtros salvos são inválidos. Salve-os novamente."
MSG_PROFILE_CONFLICT = "Não foi possível salvar o perfil de busca. Tente novamente."


# ---- errors ---------------------------------------------------------------

class SavedFiltersInvalid(Exception):
    """The stored filters JSON no longer validates against SearchFilters."""


class SearchProfileConflict(Exception):
    """The default search profile row could not be upserted."""


# ---- refresh rate limit ---------------------------------------------------

class RefreshLimiter:
    """Per-process, per-user minimum interval between refresh calls."""

    def __init__(self, interval: float = REFRESH_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.interval = interval
        self.clock = clock
        self._last: dict[int, float] = {}
        self._lock = threading.Lock()

    def acquire(self, user_id: int) -> tuple[float, float | None] | None:
        """Reserve a call; None if the previous counted one was < interval ago.

        Returns a token for `release` (used when the call ends up failing)."""
        with self._lock:
            now = self.clock()
            last = self._last.get(user_id)
            if last is not None and now - last < self.interval:
                return None
            if len(self._last) > 10_000:  # bound memory: drop expired entries
                self._last = {
                    u: t for u, t in self._last.items() if now - t < self.interval
                }
            self._last[user_id] = now
            return (now, last)

    def release(self, user_id: int, token: tuple[float, float | None]) -> None:
        """Undo a reservation so a failed call does not count."""
        now, previous = token
        with self._lock:
            if self._last.get(user_id) != now:  # superseded meanwhile
                return
            if previous is None:
                del self._last[user_id]
            else:
                self._last[user_id] = previous

    def hit(self, user_id: int) -> bool:
        """Record a call; False if the previous one was < interval ago."""
        return self.acquire(user_id) is not None

    def reset(self) -> None:
        with self._lock:
            self._last.clear()


refresh_limiter = RefreshLimiter()


def get_refresh_limiter() -> RefreshLimiter:
    """Dependency (overridden in tests)."""
    return refresh_limiter


# ---- schemas --------------------------------------------------------------

class SearchProfileOut(BaseModel):
    filters: SearchFilters


UnknownField = Literal["salary", "work_model", "country", "seniority", "language"]


class MatchJob(JobItem):
    """JobItem plus salary amounts (still no description/raw)."""

    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None


class MatchItem(BaseModel):
    job: MatchJob
    vec_score: float
    ai_score: int | None
    state: str | None
    unknown_fields: list[UnknownField]


class MatchPage(BaseModel):
    total: int
    profile_version: int | None
    vectorized: bool
    missing_embeddings: int
    chunks_used: int
    items: list[MatchItem]


class MatchDetail(JobDetail):
    vec_score: float | None
    ai_score: int | None
    state: str | None
    rationale: str | None
    strengths: list[str] | None
    gaps: list[str] | None
    error: str | None
    unknown_fields: list[UnknownField]


class RefreshOut(BaseModel):
    requested: int
    created: int
    reset: int
    already_ok: int
    profile_version: int | None


# ---- helpers --------------------------------------------------------------

def _unknown_fields(job) -> list[str]:
    out = []
    if not job.salary_known:
        out.append("salary")
    if job.work_model == "unknown":
        out.append("work_model")
    if job.country is None:
        out.append("country")
    if job.seniority == "unknown":
        out.append("seniority")
    if job.language == "unknown":
        out.append("language")
    return out


def load_filters(session: Session, user_id: int) -> SearchFilters:
    raw = session.scalar(
        select(SearchProfile.filters).where(
            SearchProfile.user_id == user_id, SearchProfile.is_default.is_(True)
        )
    )
    if raw is None:
        return SearchFilters()
    try:
        return SearchFilters.model_validate(raw)
    except ValidationError as exc:
        raise SavedFiltersInvalid() from exc


def save_filters(session: Session, user_id: int, filters: SearchFilters) -> SearchFilters:
    """Atomic upsert of the user's default search profile."""
    data = filters.model_dump(mode="json")
    stmt = insert(SearchProfile).values(
        user_id=user_id, name=DEFAULT_PROFILE_NAME, filters=data, is_default=True,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SearchProfile.user_id],
        index_where=text("is_default"),  # must match uq_search_profiles_one_default
        set_={"filters": stmt.excluded.filters, "updated_at": func.now()},
    )
    try:
        session.execute(stmt)
        session.commit()
    except IntegrityError as exc:  # e.g. a non-default row already named "default"
        session.rollback()
        raise SearchProfileConflict() from exc
    return filters


def _filters_or_409(session: Session, user_id: int) -> SearchFilters:
    try:
        return load_filters(session, user_id)
    except SavedFiltersInvalid:
        raise HTTPException(409, MSG_BAD_SAVED_FILTERS) from None


def model_version() -> str:
    """Dependency: the embedder version used for scoring (no model load)."""
    return MODEL_VERSION


# ---- routes ---------------------------------------------------------------

@router.get("/search-profile", response_model=SearchProfileOut)
def get_search_profile(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    return SearchProfileOut(filters=_filters_or_409(session, user_id))


@router.put("/search-profile", response_model=SearchProfileOut)
def put_search_profile(
    body: SearchFilters,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    try:
        saved = save_filters(session, user_id, body)
    except SearchProfileConflict:
        raise HTTPException(409, MSG_PROFILE_CONFLICT) from None
    return SearchProfileOut(filters=saved)


@router.get("/matches", response_model=MatchPage)
def list_matches(
    status: Literal["open", "closed", "all"] = "open",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    filters = _filters_or_409(session, user_id)
    page = rank_jobs(session, user_id, filters, model_version=mv,
                     status=status, limit=limit, offset=offset)
    company_ids = {r.job.company_id for r in page.items}
    names = dict(session.execute(
        select(Company.id, Company.name).where(
            Company.user_id == user_id, Company.id.in_(company_ids)
        )
    ).all()) if company_ids else {}
    items = []
    for r in page.items:
        j = r.job
        if j.user_id != user_id or j.company_id not in names:  # defensive
            continue
        job = MatchJob(
            id=j.id, company_id=j.company_id, company_name=names[j.company_id],
            title=j.title, url=j.url, location_text=j.location_text,
            country=j.country, city=j.city, work_model=j.work_model,
            seniority=j.seniority, language=j.language, department=j.department,
            salary_known=j.salary_known, status=j.status,
            first_seen_at=j.first_seen_at, posted_at=j.posted_at,
            salary_min=_f(j.salary_min), salary_max=_f(j.salary_max),
            salary_currency=j.salary_currency, salary_period=j.salary_period,
        )
        m = r.match
        items.append(MatchItem(
            job=job, vec_score=r.vec_score,
            ai_score=m.ai_score if m is not None else None,
            state=m.state if m is not None else None,
            unknown_fields=_unknown_fields(j),
        ))
    return MatchPage(
        total=page.total, profile_version=page.profile_version,
        vectorized=page.vectorized, missing_embeddings=page.missing_embeddings,
        chunks_used=page.chunks_used, items=items,
    )


@router.get("/matches/{job_id}", response_model=MatchDetail)
def match_detail(
    job_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    if not 1 <= job_id <= INT4_MAX:  # outside the int4 id column: no such job
        raise HTTPException(404, "Vaga não encontrada.")
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
    version, _ = usable_profile(session, user_id, mv)
    m = None
    if version is not None:
        m = session.scalars(
            select(Match).where(
                Match.user_id == user_id, Match.job_id == job_id,
                Match.profile_version == version,
            )
        ).first()
    return MatchDetail(
        **_item_fields(r), description=r.description,
        salary_min=_f(r.salary_min), salary_max=_f(r.salary_max),
        salary_currency=r.salary_currency, salary_period=r.salary_period,
        last_seen_at=r.last_seen_at, closed_at=r.closed_at,
        vec_score=job_vec_score(session, user_id, job_id, model_version=mv),
        ai_score=m.ai_score if m else None,
        state=m.state if m else None,
        rationale=m.rationale if m else None,
        strengths=m.strengths if m else None,
        gaps=m.gaps if m else None,
        error=m.error if m else None,
        unknown_fields=_unknown_fields(r),
    )


@router.post("/matches/refresh", response_model=RefreshOut)
def refresh_matches(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
    limiter: RefreshLimiter = Depends(get_refresh_limiter),
):
    token = limiter.acquire(user_id)
    if token is None:
        raise HTTPException(
            429, MSG_RATE_LIMITED,
            headers={"Retry-After": str(int(limiter.interval))},
        )
    try:
        version, used = usable_profile(session, user_id, mv)
        if version is None:
            raise HTTPException(409, MSG_NO_PROFILE)
        if used == 0:
            raise HTTPException(409, MSG_NOT_VECTORIZED)
        filters = _filters_or_409(session, user_id)
        res = mark_pending(session, user_id, filters, model_version=mv,
                           n=get_settings().match_top_n)
    except BaseException:
        # only calls that went through count toward the interval
        limiter.release(user_id, token)
        raise
    return RefreshOut(
        requested=res.requested, created=res.created, reset=res.reset,
        already_ok=res.already_ok, profile_version=res.profile_version,
    )
