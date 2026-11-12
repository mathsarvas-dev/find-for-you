"""Interview session service: state transitions and suggestion resolution.

Every public function commits itself at the end (like profile.service).
"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from rumo_core.interview.gaps import Gap, detect_gaps
from rumo_core.interview.suggestions import (
    SuggestionDraft,
    SuggestionTargetMissing,
    apply_suggestion,
    validate_suggestion,
)
from rumo_core.models import AnswerBankItem, ChatMessage, InterviewSession, ProfileSuggestion
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import confirm_profile, get_current_profile

MAX_ANSWER_CHARS = 4000


class InterviewError(Exception):
    message = "Erro na entrevista."

    def __init__(self, message: str | None = None):
        if message is not None:
            self.message = message
        super().__init__(self.message)


class NoProfile(InterviewError):
    message = "Confirme seu perfil antes de iniciar a entrevista."


class WrongState(InterviewError):
    message = "Esta ação não está disponível no estado atual."


class NotFound(InterviewError):
    message = "Não encontrado."


class TargetMissing(InterviewError):
    message = "O item do perfil a que esta sugestão se refere mudou ou não existe mais."


def gap_from_id(gap_id: str) -> Gap:
    """Synthetic gap parsed from an id like 'kind:target_ref' (empty hint)."""
    kind, _, ref = gap_id.partition(":")
    return Gap(
        id=gap_id, kind=kind, target_ref=None if ref in ("", "-") else ref,
        hint="", priority=0,
    )


def resolve_gap(profile: CanonicalProfile, gap_id: str) -> Gap:
    """The gap with this id in the current profile, or a synthetic one."""
    for g in detect_gaps(profile):
        if g.id == gap_id:
            return g
    return gap_from_id(gap_id)


def _load(
    session: Session, user_id: int, session_id: int, *, lock: bool = True
) -> InterviewSession:
    stmt = select(InterviewSession).where(
        InterviewSession.id == session_id, InterviewSession.user_id == user_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = session.execute(stmt.execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        session.rollback()
        raise NotFound("Sessão de entrevista não encontrada.")
    return row


def start_interview(session: Session, user_id: int) -> InterviewSession:
    latest = get_current_profile(session, user_id)
    if latest is None:
        raise NoProfile()
    active = session.scalars(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id, InterviewSession.state != "done")
        .order_by(InterviewSession.id.desc())
        .limit(1)
    ).first()
    if active is not None:
        return active
    row = InterviewSession(
        user_id=user_id, profile_version=latest.version, state="generating"
    )
    session.add(row)
    session.commit()
    return row


def submit_answer(session: Session, user_id: int, session_id: int, text: str) -> None:
    row = _load(session, user_id, session_id)
    if row.state != "awaiting_answer":
        session.rollback()
        raise WrongState("Não há pergunta aguardando resposta.")
    clean = (text or "").strip()
    if not clean:
        session.rollback()
        raise ValueError("A resposta não pode ser vazia.")
    if len(clean) > MAX_ANSWER_CHARS:
        session.rollback()
        raise ValueError(f"A resposta pode ter no máximo {MAX_ANSWER_CHARS} caracteres.")
    session.add(ChatMessage(
        user_id=user_id, session_id=row.id, role="user", kind="answer",
        content=clean, gap_id=row.current_gap_id,
    ))
    row.state = "generating"
    row.error = None
    session.commit()


def skip_question(session: Session, user_id: int, session_id: int) -> None:
    row = _load(session, user_id, session_id)
    if row.state != "awaiting_answer":
        session.rollback()
        raise WrongState("Não há pergunta aguardando resposta.")
    session.add(ChatMessage(
        user_id=user_id, session_id=row.id, role="user", kind="skip",
        content="(pulou a pergunta)", gap_id=row.current_gap_id,
    ))
    if row.current_gap_id and row.current_gap_id not in row.handled_gaps:
        row.handled_gaps = [*row.handled_gaps, row.current_gap_id]
    row.state = "generating"
    row.error = None
    session.commit()


def finish_interview(session: Session, user_id: int, session_id: int) -> None:
    row = _load(session, user_id, session_id)
    row.state = "done"
    row.error = None
    session.commit()


def retry_interview(session: Session, user_id: int, session_id: int) -> None:
    row = _load(session, user_id, session_id)
    if row.state != "error":
        session.rollback()
        raise WrongState("Só é possível tentar de novo quando houve erro.")
    row.state = "generating"
    row.error = None
    session.commit()


def resolve_suggestion(
    session: Session,
    user_id: int,
    suggestion_id: int,
    action: Literal["accept", "adjust", "reject"],
    payload: SuggestionDraft | None,
) -> ProfileSuggestion:
    sug = session.execute(
        select(ProfileSuggestion)
        .where(ProfileSuggestion.id == suggestion_id, ProfileSuggestion.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if sug is None:
        session.rollback()
        raise NotFound("Sugestão não encontrada.")
    # Row lock + status check in the same transaction: a double click (or a
    # concurrent request) sees the committed status and gets WrongState.
    if sug.status != "pending":
        session.rollback()
        raise WrongState("Esta sugestão já foi resolvida.")

    if action == "reject":
        sug.status = "rejected"
        session.commit()
        return sug

    if action == "adjust":
        if payload is None:
            session.rollback()
            raise ValueError("O ajuste precisa de uma proposta editada.")
        draft = payload
    else:
        draft = SuggestionDraft.model_validate(sug.payload)

    answer_msg = session.get(ChatMessage, sug.message_id)
    answer = answer_msg.content if answer_msg is not None else ""
    latest = get_current_profile(session, user_id)
    if latest is None:
        session.rollback()
        raise NoProfile()
    profile = CanonicalProfile.model_validate(latest.data)
    gap = resolve_gap(profile, sug.gap_id)

    reason = validate_suggestion(draft, answer=answer, profile=profile, gap=gap)
    if reason is not None:
        session.rollback()
        raise TargetMissing(f"Não foi possível aplicar a sugestão: {reason}.")

    status = "adjusted" if action == "adjust" else "accepted"
    if action == "adjust":
        sug.op = draft.op
        sug.payload = draft.model_dump()

    if draft.op == "set_preference":
        item = session.scalars(
            select(AnswerBankItem).where(
                AnswerBankItem.user_id == user_id, AnswerBankItem.key == draft.key
            )
        ).first()
        if item is None:
            session.add(AnswerBankItem(
                user_id=user_id, key=draft.key, value=draft.value.strip(),
                source_message_id=sug.message_id,
            ))
        else:
            item.value = draft.value.strip()
            item.source_message_id = sug.message_id
        sug.status = status
        session.commit()
        return sug

    try:
        new_profile = apply_suggestion(profile, draft, answer=answer, gap=gap)
    except SuggestionTargetMissing:
        session.rollback()
        raise TargetMissing() from None

    # confirm_profile commits: status and the new version land in ONE transaction.
    predicted = latest.version + 1
    sug.status = status
    sug.applied_version = predicted
    session.flush()
    row = confirm_profile(session, user_id, new_profile, narrative=latest.narrative)
    if row.version != predicted:
        sug.applied_version = row.version
        session.commit()
    return sug
