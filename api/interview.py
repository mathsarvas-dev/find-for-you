"""Interview routes: session lifecycle, answers and suggestion resolution."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.db import get_session
from rumo_core.interview import service
from rumo_core.interview.describe import describe_suggestion
from rumo_core.interview.suggestions import SuggestionDraft
from rumo_core.models import ChatMessage, InterviewSession, ProfileSuggestion
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile

router = APIRouter(prefix="/interview", tags=["interview"])

_STATUS_BY_ERROR: tuple[tuple[type[service.InterviewError], int], ...] = (
    (service.NotFound, 404),
    (service.InvalidSuggestion, 422),
    (service.NoProfile, 409),
    (service.WrongState, 409),
    (service.TargetMissing, 409),
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, service.InterviewError):
        for cls, code in _STATUS_BY_ERROR:
            if isinstance(exc, cls):
                return HTTPException(code, exc.message)
        return HTTPException(409, exc.message)
    return HTTPException(422, str(exc))


class SessionCreated(BaseModel):
    id: int
    state: str


class StateOut(BaseModel):
    state: str


class AnswerIn(BaseModel):
    text: str


class ResolveIn(BaseModel):
    action: Literal["accept", "adjust", "reject"]
    payload: SuggestionDraft | None = None


class ResolveOut(BaseModel):
    status: str
    applied_version: int | None


class Progress(BaseModel):
    asked: int
    max: int


class MessageOut(BaseModel):
    id: int
    role: str
    kind: str
    content: str
    gap_id: str | None


class SuggestionOut(BaseModel):
    id: int
    message_id: int
    gap_id: str
    op: str
    payload: dict
    status: str
    applied_version: int | None
    before: str
    after: str


class CurrentOut(BaseModel):
    id: int
    state: str
    error: str | None
    progress: Progress
    messages: list[MessageOut]
    suggestions: list[SuggestionOut]


@router.post("", response_model=SessionCreated)
def start(
    response: Response,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    had_active = session.scalars(
        select(InterviewSession.id)
        .where(InterviewSession.user_id == user_id, InterviewSession.state != "done")
        .limit(1)
    ).first() is not None
    try:
        row = service.start_interview(session, user_id)
    except service.InterviewError as exc:
        raise _http_error(exc)
    response.status_code = 200 if had_active else 201
    return SessionCreated(id=row.id, state=row.state)


@router.get("/current", response_model=CurrentOut)
def current(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    rows = session.scalars(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id)
        .order_by(InterviewSession.id.desc())
    ).all()
    if not rows:
        raise HTTPException(404, "Nenhuma entrevista encontrada.")
    row = next((r for r in rows if r.state != "done"), rows[0])
    messages = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == row.id, ChatMessage.user_id == user_id)
        .order_by(ChatMessage.id)
    ).all()
    suggestions = session.scalars(
        select(ProfileSuggestion)
        .where(ProfileSuggestion.session_id == row.id, ProfileSuggestion.user_id == user_id)
        .order_by(ProfileSuggestion.id)
    ).all()
    latest = get_current_profile(session, user_id)
    profile = CanonicalProfile.model_validate(latest.data) if latest is not None else None
    out_suggestions = []
    for s in suggestions:
        before, after = describe_suggestion(profile, SuggestionDraft.model_validate(s.payload))
        out_suggestions.append(SuggestionOut(
            id=s.id, message_id=s.message_id, gap_id=s.gap_id, op=s.op, payload=s.payload,
            status=s.status, applied_version=s.applied_version, before=before, after=after,
        ))
    return CurrentOut(
        id=row.id, state=row.state, error=row.error,
        progress=Progress(asked=row.asked_count, max=row.max_questions),
        messages=[
            MessageOut(id=m.id, role=m.role, kind=m.kind, content=m.content, gap_id=m.gap_id)
            for m in messages
        ],
        suggestions=out_suggestions,
    )


def _state(session: Session, user_id: int, session_id: int) -> StateOut:
    row = session.get(InterviewSession, session_id, populate_existing=True)
    return StateOut(state=row.state)


@router.post("/{session_id}/answer", status_code=202, response_model=StateOut)
def answer(
    session_id: int,
    body: AnswerIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    try:
        service.submit_answer(session, user_id, session_id, body.text)
    except (service.InterviewError, ValueError) as exc:
        raise _http_error(exc)
    return _state(session, user_id, session_id)


def _transition(name: str):
    fn = getattr(service, name)

    def route(
        session_id: int,
        session: Session = Depends(get_session),
        user_id: int = Depends(current_user_id),
    ):
        try:
            fn(session, user_id, session_id)
        except service.InterviewError as exc:
            raise _http_error(exc)
        return _state(session, user_id, session_id)

    route.__name__ = name
    return route


for _path, _fn in (("skip", "skip_question"), ("finish", "finish_interview"),
                   ("retry", "retry_interview")):
    router.add_api_route(
        f"/{{session_id}}/{_path}", _transition(_fn), methods=["POST"], response_model=StateOut
    )


@router.post("/suggestions/{suggestion_id}/resolve", response_model=ResolveOut)
def resolve(
    suggestion_id: int,
    body: ResolveIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    try:
        sug = service.resolve_suggestion(
            session, user_id, suggestion_id, body.action, body.payload
        )
    except (service.InterviewError, ValueError) as exc:
        raise _http_error(exc)
    return ResolveOut(status=sug.status, applied_version=sug.applied_version)
