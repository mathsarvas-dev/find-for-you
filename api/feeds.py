"""Aggregator feed routes: the user's saved feeds and the registered feed sources.

Every feed goes through the compliance gate (`policy.json` + the user's choice)
before it is saved. Key values are never returned: only "configured" or not.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.models import JobFeed
from rumo_core.sources.feeds import (
    FeedParams,
    feed_attribution,
    feed_names,
    feed_needs_key,
    get_feed,
)
from rumo_core.sources.portals.gate import check, load_policy
from rumo_core.sources.portals.state import load_states

router = APIRouter(tags=["feeds"])

MAX_FEEDS = 20
MSG_UNKNOWN_SOURCE = "Fonte desconhecida."
MSG_NO_KEY = "Chave da API não configurada no .env."
MSG_DUPLICATE = "Esse feed já existe."
MSG_LIMIT = "Limite de 20 feeds atingido."
MSG_NOT_FOUND = "Feed não encontrado."
_CREDIT_PREFIX = "Vagas via "


class Attribution(BaseModel):
    label: str
    url: str


class FeedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(max_length=32)
    params: dict = Field(default_factory=dict)


class FeedOut(BaseModel):
    id: int
    source: str
    params: dict
    active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_error: str | None
    jobs_seen: int
    attribution: Attribution | None


class FeedSourceOut(BaseModel):
    source: str
    label: str
    needs_key: bool
    key_configured: bool
    gate_status: str
    gate_allowed: bool
    attribution: Attribution | None


class FeedsOut(BaseModel):
    feeds: list[FeedOut]
    sources: list[FeedSourceOut]


def _attribution(source: str) -> Attribution | None:
    found = feed_attribution(source)
    return Attribution(label=found.label, url=found.url) if found is not None else None


def _feed_out(row: JobFeed) -> FeedOut:
    return FeedOut(
        id=row.id, source=row.source, params=row.params,
        active=row.active, last_run_at=row.last_run_at, next_run_at=row.next_run_at,
        last_error=row.last_error, jobs_seen=row.jobs_seen, attribution=_attribution(row.source),
    )


def _key_configured(name: str) -> bool:
    return bool(get_feed(name).key_configured(get_settings()))


def _gate_verdict(session: Session, user_id: int, source: str):
    state = load_states(session, user_id).get(source)
    return check(source, state=state)


def _policy_status(source: str) -> str:
    entry = load_policy().get(source)
    return entry.status if entry is not None else "blocked"


@router.get("/feeds", response_model=FeedsOut)
def list_feeds(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    rows = session.scalars(
        select(JobFeed).where(JobFeed.user_id == user_id).order_by(JobFeed.id)
    ).all()
    sources = []
    for name in feed_names():
        attribution = _attribution(name)
        needs_key = feed_needs_key(name)
        sources.append(FeedSourceOut(
            source=name,
            label=(attribution.label.removeprefix(_CREDIT_PREFIX) if attribution else name),
            needs_key=needs_key,
            key_configured=_key_configured(name) if needs_key else True,
            gate_status=_policy_status(name),
            gate_allowed=_gate_verdict(session, user_id, name).allowed,
            attribution=attribution,
        ))
    return FeedsOut(feeds=[_feed_out(r) for r in rows], sources=sources)


@router.post("/feeds", status_code=201, response_model=FeedOut)
def create_feed(
    body: FeedIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    if body.source not in feed_names():
        raise HTTPException(422, MSG_UNKNOWN_SOURCE)
    try:
        params = FeedParams.from_dict(body.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if feed_needs_key(body.source) and not _key_configured(body.source):
        raise HTTPException(422, MSG_NO_KEY)
    verdict = _gate_verdict(session, user_id, body.source)
    if not verdict.allowed:
        raise HTTPException(409, verdict.message)
    count = session.scalar(
        select(func.count()).select_from(JobFeed).where(JobFeed.user_id == user_id)
    )
    if (count or 0) >= MAX_FEEDS:
        raise HTTPException(422, MSG_LIMIT)
    payload = params.to_dict()
    dup = session.scalar(
        select(JobFeed.id).where(
            JobFeed.user_id == user_id, JobFeed.source == body.source,
            JobFeed.params == payload,
        )
    )
    if dup is not None:
        raise HTTPException(409, MSG_DUPLICATE)
    row = JobFeed(
        user_id=user_id, source=body.source, params=payload, active=True,
        next_run_at=datetime.now(timezone.utc),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, MSG_DUPLICATE) from None
    session.refresh(row)
    return _feed_out(row)


@router.delete("/feeds/{feed_id}", status_code=204)
def delete_feed(
    feed_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = session.scalar(
        select(JobFeed).where(JobFeed.id == feed_id, JobFeed.user_id == user_id)
    )
    if row is None:
        raise HTTPException(404, MSG_NOT_FOUND)
    session.delete(row)
    session.commit()
    return Response(status_code=204)
