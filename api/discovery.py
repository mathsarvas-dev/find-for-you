"""Automatic company discovery routes: preferences, counters and candidates.

Every query is scoped by the caller's user_id. GET /discovery never creates a
preference row (missing row = enabled, no target companies); PUT upserts
atomically.
"""

import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.models import Company, DiscoveryCandidate, UserPreference

router = APIRouter(tags=["discovery"])

MAX_TARGETS = 50
MAX_NAME_LEN = 80

CandidateState = Literal["pending", "valid", "invalid", "known", "rejected"]


class DiscoveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    target_companies: list[str] = Field(max_length=MAX_TARGETS)

    @field_validator("target_companies")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v:
            name = raw.strip()
            if not name:
                raise ValueError("Nome de empresa não pode ser vazio.")
            if len(name) > MAX_NAME_LEN:
                raise ValueError(
                    f"Nome de empresa deve ter no máximo {MAX_NAME_LEN} caracteres."
                )
            if any(unicodedata.category(ch) == "Cc" for ch in name):
                raise ValueError("Nome de empresa contém caracteres inválidos.")
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out


class DiscoveryOut(BaseModel):
    enabled: bool
    target_companies: list[str]
    auto_companies: int
    max_auto: int
    pending: int
    found: int


class CandidateOut(BaseModel):
    token: str
    name_hint: str | None
    origin_kind: str
    state: str
    attempts: int
    last_error: str | None


class CandidatePage(BaseModel):
    total: int
    items: list[CandidateOut]


def _state_count(session: Session, user_id: int, state: str) -> int:
    return session.scalar(
        select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.user_id == user_id, DiscoveryCandidate.state == state
        )
    ) or 0


def _build(session: Session, user_id: int) -> DiscoveryOut:
    pref = session.scalars(
        select(UserPreference).where(UserPreference.user_id == user_id)
    ).first()
    auto = session.scalar(
        select(func.count()).select_from(Company).where(
            Company.user_id == user_id, Company.origin == "auto",
            Company.active.is_(True),
        )
    ) or 0
    return DiscoveryOut(
        enabled=pref.discovery_enabled if pref is not None else True,
        target_companies=list(pref.target_companies) if pref is not None else [],
        auto_companies=auto,
        max_auto=get_settings().discovery_max_auto_companies,
        pending=_state_count(session, user_id, "pending"),
        found=_state_count(session, user_id, "valid"),
    )


@router.get("/discovery", response_model=DiscoveryOut)
def get_discovery(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    return _build(session, user_id)


@router.put("/discovery", response_model=DiscoveryOut)
def put_discovery(
    body: DiscoveryIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    stmt = insert(UserPreference).values(
        user_id=user_id, discovery_enabled=body.enabled,
        target_companies=body.target_companies,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserPreference.user_id],
        set_={
            "discovery_enabled": stmt.excluded.discovery_enabled,
            "target_companies": stmt.excluded.target_companies,
            "updated_at": func.now(),
        },
    )
    session.execute(stmt)
    session.commit()
    return _build(session, user_id)


@router.get("/discovery/candidates", response_model=CandidatePage)
def list_candidates(
    state: CandidateState | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    conds = [DiscoveryCandidate.user_id == user_id]
    if state is not None:
        conds.append(DiscoveryCandidate.state == state)
    total = session.scalar(
        select(func.count()).select_from(DiscoveryCandidate).where(*conds)
    ) or 0
    rows = session.scalars(
        select(DiscoveryCandidate).where(*conds)
        .order_by(DiscoveryCandidate.id).limit(limit).offset(offset)
    ).all()
    return CandidatePage(total=total, items=[
        CandidateOut(
            token=r.token, name_hint=r.name_hint, origin_kind=r.origin_kind,
            state=r.state, attempts=r.attempts, last_error=r.last_error,
        ) for r in rows
    ])
