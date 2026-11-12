"""Worker job: one interview turn per tick (question generation + suggestion)."""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select, update

from rumo_core.interview.gaps import Gap, detect_gaps
from rumo_core.interview.service import resolve_gap
from rumo_core.interview.suggestions import SuggestionDraft
from rumo_core.interview.turn import run_turn
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMProvider, LLMRateLimited
from rumo_core.models import (
    AnswerBankItem,
    ChatMessage,
    InterviewSession,
    ProfileSuggestion,
)
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile

logger = logging.getLogger("worker.interview_jobs")

MSG_INVALID = "Não consegui gerar a próxima pergunta. Tente novamente."
MSG_LLM_FAIL = "Falha ao gerar a próxima pergunta. Tente novamente mais tarde."
MSG_UNEXPECTED = "Falha inesperada na entrevista."
MSG_DONE = (
    "Terminamos por aqui. Revise as sugestões pendentes e confirme o que fizer sentido."
)

RATE_LIMIT_PAUSE_SECONDS = 60.0

_warned_no_llm = False
_pause_until = 0.0


def _reset_state() -> None:
    """Reset in-process state (used by tests)."""
    global _warned_no_llm, _pause_until
    _warned_no_llm = False
    _pause_until = 0.0


def reset_working_sessions(session_factory, *, user_id: int | None = None) -> int:
    """On startup: sessions left 'working' by a dead worker go back to 'generating'.

    Assumes a single worker process: with several workers this would also reset
    sessions another live worker is processing.
    """
    with session_factory() as s:
        stmt = update(InterviewSession).where(InterviewSession.state == "working")
        if user_id is not None:
            stmt = stmt.where(InterviewSession.user_id == user_id)
        res = s.execute(stmt.values(state="generating"))
        s.commit()
        return res.rowcount


def _claim(session_factory, user_id: int | None) -> int | None:
    with session_factory() as s:
        stmt = select(InterviewSession).where(InterviewSession.state == "generating")
        if user_id is not None:
            stmt = stmt.where(InterviewSession.user_id == user_id)
        row = s.execute(
            stmt.order_by(InterviewSession.id).limit(1).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.state = "working"
        s.commit()
        return row.id


def _set_state(session_factory, session_id: int, state: str, error: str | None = None) -> None:
    """Leave 'working' (only if still working: a user may have finished meanwhile)."""
    with session_factory() as s:
        s.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id, InterviewSession.state == "working")
            .values(state=state, error=error)
        )
        s.commit()


@dataclass
class _Prepared:
    user_id: int
    profile: CanonicalProfile
    answered_gap: Gap | None
    answer: str | None
    answer_message_id: int | None
    next_gap: Gap | None
    handled: list[str]
    finishing: bool  # no more questions: close after processing the answer


def _prepare(session_factory, session_id: int) -> _Prepared:
    with session_factory() as s:
        sess = s.get(InterviewSession, session_id)
        profile_row = get_current_profile(s, sess.user_id)
        if profile_row is None:
            raise RuntimeError("interview session without a profile")
        profile = CanonicalProfile.model_validate(profile_row.data)
        last = s.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(1)
        ).first()
        answered_gap = answer = answer_message_id = None
        if last is not None and last.role == "user":
            if sess.current_gap_id:
                answered_gap = resolve_gap(profile, sess.current_gap_id)
            if last.kind == "answer":
                answer, answer_message_id = last.content, last.id
        prefs = frozenset(s.scalars(
            select(AnswerBankItem.key).where(AnswerBankItem.user_id == sess.user_id)
        ))
        handled = list(sess.handled_gaps)
        skip_ids = set(handled)
        if answered_gap is not None:
            skip_ids.add(answered_gap.id)
        next_gap = next(
            (g for g in detect_gaps(profile, prefs) if g.id not in skip_ids), None
        )
        finishing = next_gap is None or sess.asked_count >= sess.max_questions
        return _Prepared(
            user_id=sess.user_id, profile=profile, answered_gap=answered_gap,
            answer=answer, answer_message_id=answer_message_id,
            next_gap=None if finishing else next_gap, handled=handled,
            finishing=finishing,
        )


def _finalize(
    session_factory, session_id: int, p: _Prepared,
    suggestion: SuggestionDraft | None, question: str | None,
) -> None:
    """Everything the turn produced, in ONE transaction."""
    with session_factory() as s:
        sess = s.execute(
            select(InterviewSession)
            .where(InterviewSession.id == session_id)
            .with_for_update()
        ).scalar_one()
        if sess.state != "working":  # finished/changed by the user meanwhile
            s.rollback()
            return
        if suggestion is not None and p.answer_message_id is not None and p.answered_gap:
            s.add(ProfileSuggestion(
                user_id=p.user_id, session_id=session_id,
                message_id=p.answer_message_id, gap_id=p.answered_gap.id,
                op=suggestion.op, payload=suggestion.model_dump(), status="pending",
            ))
        handled = list(p.handled)
        if p.answered_gap is not None and p.answered_gap.id not in handled:
            handled.append(p.answered_gap.id)
        sess.handled_gaps = handled
        if p.next_gap is None or question is None:
            s.add(ChatMessage(
                user_id=p.user_id, session_id=session_id, role="assistant",
                kind="info", content=MSG_DONE,
            ))
            sess.state = "done"
            sess.current_gap_id = None
        else:
            s.add(ChatMessage(
                user_id=p.user_id, session_id=session_id, role="assistant",
                kind="question", content=question, gap_id=p.next_gap.id,
            ))
            sess.asked_count += 1
            sess.current_gap_id = p.next_gap.id
            sess.state = "awaiting_answer"
        sess.error = None
        s.commit()


async def process_interview_turns(
    session_factory,
    llm: LLMProvider | None,
    *,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Process at most one 'generating' session per call; returns how many.

    No LLM (no API key): sessions stay 'generating', warns once. After a rate
    limit the job pauses itself for RATE_LIMIT_PAUSE_SECONDS.
    """
    global _warned_no_llm, _pause_until
    if llm is None:
        if not _warned_no_llm:
            logger.warning("GROQ_API_KEY not set: interviews stay generating")
            _warned_no_llm = True
        return 0
    if clock() < _pause_until:
        return 0
    session_id = _claim(session_factory, user_id)
    if session_id is None:
        return 0
    try:
        p = _prepare(session_factory, session_id)
        suggestion = question = None
        if p.next_gap is not None or p.answer is not None:
            outcome = await run_turn(
                llm, profile=p.profile, answered_gap=p.answered_gap,
                answer=p.answer, next_gap=p.next_gap,
            )
            suggestion, question = outcome.suggestion, outcome.question
        _finalize(session_factory, session_id, p, suggestion, question)
    except LLMRateLimited:
        _pause_until = clock() + RATE_LIMIT_PAUSE_SECONDS
        logger.warning(
            "LLM rate limited; pausing interview for %ds", RATE_LIMIT_PAUSE_SECONDS
        )
        _revert(session_factory, session_id)
        return 0
    except LLMInvalidResponse as exc:
        # class name and session id only: the message may echo user text
        logger.warning(
            "interview %s invalid LLM response (%s)", session_id, type(exc).__name__
        )
        _set_state(session_factory, session_id, "error", MSG_INVALID)
        return 1
    except LLMError:
        logger.exception("interview %s LLM failure", session_id)
        _set_state(session_factory, session_id, "error", MSG_LLM_FAIL)
        return 1
    except Exception:
        logger.exception("interview %s unexpected failure", session_id)
        _set_state(session_factory, session_id, "error", MSG_UNEXPECTED)
        return 1
    except BaseException:
        # cancellation: do not leave it stuck in 'working'
        _revert(session_factory, session_id)
        raise
    return 1


def _revert(session_factory, session_id: int) -> None:
    _set_state(session_factory, session_id, "generating")
