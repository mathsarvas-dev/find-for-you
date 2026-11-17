"""LinkedIn profile optimizer (R10).

Works from the confirmed canonical profile only; it never reads LinkedIn. The
model call is interactive (same Groq provider, rate-limit handling and
user-facing errors as POST /filters/parse); the latest result is kept per user
so a reload does not lose it. A provisional profile (pasted text, never
extracted) is refused: there is nothing reliable to cite.
"""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from api.chat_filters import HourlyLimiter, get_filters_llm
from api.deps import current_user_id
from rumo_core.db import get_session
from rumo_core.linkedin.optimizer import (
    Optimization,
    OptimizationInvalid,
    optimize_profile,
)
from rumo_core.llm.base import LLMError, LLMProvider, LLMRateLimited
from rumo_core.models import LinkedinOptimization, Profile
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile

logger = logging.getLogger(__name__)

router = APIRouter(tags=["linkedin"])

MSG_NO_PROFILE = "Confirme seu perfil antes de otimizar o LinkedIn."
MSG_PROVISIONAL = (
    "Seu perfil veio de um texto colado, sem extração. "
    "Confirme o perfil antes de otimizar o LinkedIn."
)
MSG_NO_LLM = "A otimização do LinkedIn não está configurada. Falta a chave do serviço de IA."
MSG_LLM_BUSY = "O serviço de IA está ocupado. Tente em instantes."
MSG_LIMIT = "Você usou as 5 otimizações desta hora. Tente mais tarde."
MSG_INVALID = (
    "Não consegui escrever sugestões sem inventar dados. "
    "Complete o perfil com mais detalhes e tente de novo."
)

OPTIMIZE_TIMEOUT_SECONDS = 90

linkedin_limiter = HourlyLimiter(max_calls=5)


def get_linkedin_limiter() -> HourlyLimiter:
    """Dependency (overridden in tests)."""
    return linkedin_limiter


def get_linkedin_llm() -> LLMProvider | None:
    """Dependency: the shared interactive provider; None without an API key."""
    return get_filters_llm()


class OptimizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["pt", "en", "de"] = "pt"


class OptimizationOut(BaseModel):
    profile_version: int
    language: str
    #: the profile was confirmed again after this run: the suggestions may be out of date
    stale: bool
    created_at: str
    result: Optimization


class OptimizationState(BaseModel):
    optimization: OptimizationOut | None = None


def _out(row: LinkedinOptimization, current_version: int | None) -> OptimizationOut:
    return OptimizationOut(
        profile_version=row.profile_version, language=row.language,
        stale=current_version is not None and current_version != row.profile_version,
        created_at=row.updated_at.isoformat(),
        result=Optimization.model_validate(row.data),
    )


def _current_version(session: Session, user_id: int) -> int | None:
    p = get_current_profile(session, user_id)
    return p.version if p is not None else None


def _load(session: Session, user_id: int) -> OptimizationState:
    row = session.scalar(select(LinkedinOptimization).where(LinkedinOptimization.user_id == user_id))
    if row is None:
        return OptimizationState()
    try:
        return OptimizationState(optimization=_out(row, _current_version(session, user_id)))
    except ValueError:  # stored JSON from an older shape: treat as none
        return OptimizationState()


def _profile_for_optimizer(session: Session, user_id: int) -> tuple[Profile, CanonicalProfile]:
    prof = get_current_profile(session, user_id)
    if prof is None:
        raise HTTPException(409, MSG_NO_PROFILE)
    if prof.provisional:
        raise HTTPException(409, MSG_PROVISIONAL)
    return prof, CanonicalProfile.model_validate(prof.data)


def _save(session: Session, user_id: int, version: int, language: str,
          result: Optimization) -> OptimizationState:
    data = result.model_dump(mode="json")
    stmt = insert(LinkedinOptimization).values(
        user_id=user_id, profile_version=version, language=language, data=data)
    stmt = stmt.on_conflict_do_update(
        index_elements=[LinkedinOptimization.user_id],
        set_={"profile_version": version, "language": language, "data": data,
              "updated_at": func.now()},
    )
    session.execute(stmt)
    session.commit()
    return _load(session, user_id)


@router.get("/linkedin/optimization", response_model=OptimizationState)
def get_optimization(session: Session = Depends(get_session),
                     user_id: int = Depends(current_user_id)):
    return _load(session, user_id)


@router.post("/linkedin/optimization", response_model=OptimizationState)
async def optimize(
    body: OptimizeIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    llm: LLMProvider | None = Depends(get_linkedin_llm),
    limiter: HourlyLimiter = Depends(get_linkedin_limiter),
):
    prof, profile = await run_in_threadpool(_profile_for_optimizer, session, user_id)
    version = prof.version
    if llm is None:
        raise HTTPException(503, MSG_NO_LLM)
    if not limiter.acquire(user_id):
        raise HTTPException(
            429, MSG_LIMIT,
            headers={"Retry-After": str(max(1, limiter.retry_after(user_id)))},
        )
    try:
        result = await asyncio.wait_for(
            optimize_profile(llm, profile, locale=body.language), OPTIMIZE_TIMEOUT_SECONDS)
    except OptimizationInvalid:
        raise HTTPException(422, MSG_INVALID) from None
    except (LLMRateLimited, LLMError, TimeoutError) as exc:
        logger.warning("linkedin optimize LLM failure: %s", type(exc).__name__)
        raise HTTPException(503, MSG_LLM_BUSY) from None
    return await run_in_threadpool(_save, session, user_id, version, body.language, result)
