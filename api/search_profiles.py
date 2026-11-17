"""Named search profiles (R11).

Each user can keep several named profiles (filters + taste query). Exactly one
is *active* (`is_default`): every consumer of "the search profile" (matches,
funnel, GET/PUT /search-profile) keeps reading that row, so switching only
moves the flag. Everything is scoped by the caller's user_id.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.matching import DEFAULT_PROFILE_NAME, save_filters
from rumo_core.db import get_session
from rumo_core.matching.filters import SearchFilters
from rumo_core.models import SearchProfile
from rumo_core.sources.funnel import mark_for_reevaluation

router = APIRouter(tags=["search-profiles"])

MAX_PROFILES = 20
MAX_NAME = 60

MSG_NAME_TAKEN = "Já existe um perfil de busca com esse nome. Escolha outro nome."
MSG_TOO_MANY = f"Você já tem {MAX_PROFILES} perfis de busca. Exclua algum antes de criar outro."
MSG_NOT_FOUND = "Perfil de busca não encontrado."
MSG_DELETE_ACTIVE = "Ative outro perfil de busca antes de excluir este."
MSG_BAD_NAME = "O nome deve ter de 1 a 60 caracteres, sem quebras de linha."


def _clean_name(v: str) -> str:
    v = v.strip()
    if not v or len(v) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError(MSG_BAD_NAME)
    return v


class NameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)


class CreateIn(NameIn):
    """New profile = a copy of the active one (filters and taste query)."""

    activate: bool = False


class SearchProfileItem(BaseModel):
    id: int
    name: str
    active: bool
    filters: SearchFilters
    query_text: str | None = None
    updated_at: datetime


class SearchProfileList(BaseModel):
    items: list[SearchProfileItem]


def _item(row: SearchProfile) -> SearchProfileItem:
    try:
        filters = SearchFilters.model_validate(row.filters or {})
    except Exception:  # invalid stored JSON: show it empty rather than failing the list
        filters = SearchFilters()
    return SearchProfileItem(
        id=row.id, name=row.name, active=bool(row.is_default), filters=filters,
        query_text=row.query_text, updated_at=row.updated_at,
    )


def _rows(session: Session, user_id: int) -> list[SearchProfile]:
    return list(session.scalars(
        select(SearchProfile).where(SearchProfile.user_id == user_id)
        .order_by(SearchProfile.is_default.desc(), func.lower(SearchProfile.name),
                  SearchProfile.id)
    ))


def _owned(session: Session, user_id: int, pid: int) -> SearchProfile:
    row = session.scalar(select(SearchProfile).where(
        SearchProfile.id == pid, SearchProfile.user_id == user_id))
    if row is None:
        raise HTTPException(404, MSG_NOT_FOUND)
    return row


def _name_taken(rows: list[SearchProfile], name: str, *, except_id: int | None = None) -> bool:
    low = name.casefold()
    return any(r.name.casefold() == low and r.id != except_id for r in rows)


@router.get("/search-profiles", response_model=SearchProfileList)
def list_profiles(session: Session = Depends(get_session),
                  user_id: int = Depends(current_user_id)):
    return SearchProfileList(items=[_item(r) for r in _rows(session, user_id)])


@router.post("/search-profiles", response_model=SearchProfileItem, status_code=201)
def create_profile(body: CreateIn, session: Session = Depends(get_session),
                   user_id: int = Depends(current_user_id)):
    rows = _rows(session, user_id)
    if not any(r.is_default for r in rows):
        # first named profile: materialize the implicit default one
        save_filters(session, user_id, SearchFilters())
        rows = _rows(session, user_id)
    if len(rows) >= MAX_PROFILES:
        raise HTTPException(409, MSG_TOO_MANY)
    if _name_taken(rows, body.name):
        raise HTTPException(409, MSG_NAME_TAKEN)
    active = next(r for r in rows if r.is_default)
    new = SearchProfile(
        user_id=user_id, name=body.name, filters=active.filters,
        query_text=active.query_text, query_embedding=active.query_embedding,
        query_model_version=active.query_model_version, is_default=False,
    )
    session.add(new)
    try:
        session.flush()
        if body.activate:
            _switch(session, user_id, new, active)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, MSG_NAME_TAKEN) from None
    session.refresh(new)
    return _item(new)


def _switch(session: Session, user_id: int, target: SearchProfile,
            current: SearchProfile | None) -> None:
    """Move the active flag to `target` (caller commits)."""
    if current is not None and current.id == target.id:
        return
    old_filters = current.filters if current is not None else None
    session.execute(update(SearchProfile).where(
        SearchProfile.user_id == user_id, SearchProfile.is_default.is_(True),
    ).values(is_default=False))
    session.execute(update(SearchProfile).where(
        SearchProfile.id == target.id).values(is_default=True))
    session.expire(target)
    if old_filters != target.filters:
        # other filters: stubs may now pass, so their sources are collected again
        mark_for_reevaluation(session, user_id)


@router.post("/search-profiles/{pid}/activate", response_model=SearchProfileItem)
def activate_profile(pid: int, session: Session = Depends(get_session),
                     user_id: int = Depends(current_user_id)):
    target = _owned(session, user_id, pid)
    current = session.scalar(select(SearchProfile).where(
        SearchProfile.user_id == user_id, SearchProfile.is_default.is_(True)))
    _switch(session, user_id, target, current)
    session.commit()
    session.refresh(target)
    return _item(target)


@router.patch("/search-profiles/{pid}", response_model=SearchProfileItem)
def rename_profile(pid: int, body: NameIn, session: Session = Depends(get_session),
                   user_id: int = Depends(current_user_id)):
    row = _owned(session, user_id, pid)
    if _name_taken(_rows(session, user_id), body.name, except_id=row.id):
        raise HTTPException(409, MSG_NAME_TAKEN)
    row.name = body.name
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, MSG_NAME_TAKEN) from None
    session.refresh(row)
    return _item(row)


@router.delete("/search-profiles/{pid}", status_code=204)
def delete_profile(pid: int, session: Session = Depends(get_session),
                   user_id: int = Depends(current_user_id)):
    row = _owned(session, user_id, pid)
    if row.is_default:
        raise HTTPException(409, MSG_DELETE_ACTIVE)
    session.delete(row)
    session.commit()
    return Response(status_code=204)
