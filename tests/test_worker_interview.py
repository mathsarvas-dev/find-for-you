import asyncio
import uuid

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.interview.suggestions import SuggestionDraft
from rumo_core.interview.turn import TurnResult
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMRateLimited
from rumo_core.models import (
    AnswerBankItem,
    ChatMessage,
    InterviewSession,
    Profile,
    ProfileChunk,
    ProfileSuggestion,
    User,
)
from rumo_core.profile.schema import Achievement, Basics, CanonicalProfile, Experience, Skill
from rumo_core.profile.service import confirm_profile
from worker import interview_jobs as ij
from worker.interview_jobs import (
    process_interview_turns,
    reset_working_sessions,
)

pytestmark = pytest.mark.db

GAP1 = "achievement_without_number:e1"
GAP2 = "pref_salary_expectation:-"


@pytest.fixture(autouse=True)
def _fresh_state():
    ij._reset_state()
    yield
    ij._reset_state()


def _profile(with_number=False):
    ach = [Achievement(id="a1", text="Reduzi custos em 20%")] if with_number else []
    return CanonicalProfile(
        basics=Basics(headline="Dev"), summary="Dev backend",
        experiences=[Experience(id="e1", title="Dev", company="Acme", start="2019",
                                description="Backend", achievements=ach)],
        skills=[Skill(id="s1", name="Python")],
    )


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"worker-interview-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        for model in (ProfileSuggestion, ChatMessage, AnswerBankItem, InterviewSession,
                      ProfileChunk, Profile):
            s.execute(delete(model).where(model.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def _new_session(uid, state="generating", profile=None, **kw):
    with SessionLocal() as s:
        confirm_profile(s, uid, profile or _profile(), narrative=None)
        iv = InterviewSession(user_id=uid, profile_version=1, state=state, **kw)
        s.add(iv)
        s.commit()
        return iv.id


def _get(sid):
    with SessionLocal() as s:
        return s.get(InterviewSession, sid)


def _msgs(sid):
    with SessionLocal() as s:
        return list(s.scalars(
            select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.id)
        ))


def _sugs(sid):
    with SessionLocal() as s:
        return list(s.scalars(
            select(ProfileSuggestion).where(ProfileSuggestion.session_id == sid)
        ))


def _answer(uid, sid, text, kind="answer", gap=GAP1):
    with SessionLocal() as s:
        s.add(ChatMessage(user_id=uid, session_id=sid, role="user", kind=kind,
                          content=text, gap_id=gap))
        iv = s.get(InterviewSession, sid)
        iv.state = "generating"
        s.commit()


class FakeLLM:
    def __init__(self, *results, exc=None):
        self.results, self.exc, self.calls = list(results), exc, []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return self.results.pop(0)


async def _run(uid, llm, **kw):
    return await process_interview_turns(SessionLocal, llm, user_id=uid, **kw)


async def test_first_turn_creates_question_and_awaiting_state(uid):
    sid = _new_session(uid)
    llm = FakeLLM(TurnResult(next_question="Qual resultado você teve?"))
    assert await _run(uid, llm) == 1
    iv = _get(sid)
    assert iv.state == "awaiting_answer" and iv.asked_count == 1
    assert iv.current_gap_id == GAP1 and iv.handled_gaps == []
    (m,) = _msgs(sid)
    assert (m.role, m.kind, m.content, m.gap_id) == (
        "assistant", "question", "Qual resultado você teve?", GAP1)
    assert _sugs(sid) == []


async def test_answer_turn_creates_suggestion_and_next_question(uid):
    sid = _new_session(uid, state="awaiting_answer", asked_count=1, current_gap_id=GAP1)
    _answer(uid, sid, "Reduzi a latência em 30%")
    draft = SuggestionDraft(op="append_achievement", target_ref="e1",
                            text="Reduzi a latência em 30%")
    llm = FakeLLM(TurnResult(suggestion=draft, next_question="Qual sua pretensão?"))
    assert await _run(uid, llm) == 1
    iv = _get(sid)
    assert iv.state == "awaiting_answer" and iv.asked_count == 2
    assert iv.current_gap_id == GAP2 and iv.handled_gaps == [GAP1]
    msgs = _msgs(sid)
    assert msgs[-1].kind == "question" and msgs[-1].gap_id == GAP2
    (sug,) = _sugs(sid)
    assert sug.status == "pending" and sug.gap_id == GAP1
    assert sug.message_id == msgs[0].id and sug.op == "append_achievement"
    assert sug.payload == draft.model_dump()


async def test_skip_turn_has_no_suggestion_but_asks_next(uid):
    sid = _new_session(uid, state="awaiting_answer", asked_count=1, current_gap_id=GAP1,
                       handled_gaps=[GAP1])
    _answer(uid, sid, "(pulou)", kind="skip")
    llm = FakeLLM(TurnResult(next_question="Qual sua pretensão?"))
    assert await _run(uid, llm) == 1
    iv = _get(sid)
    assert iv.current_gap_id == GAP2 and iv.state == "awaiting_answer"
    assert _sugs(sid) == []


async def test_invented_number_suggestion_not_stored(uid):
    sid = _new_session(uid, state="awaiting_answer", asked_count=1, current_gap_id=GAP1)
    _answer(uid, sid, "Reduzi a latência em 30%")
    bad = SuggestionDraft(op="append_achievement", target_ref="e1",
                          text="Reduzi a latência em 45%")
    llm = FakeLLM(TurnResult(suggestion=bad, next_question="Qual sua pretensão?"))
    assert await _run(uid, llm) == 1
    assert _sugs(sid) == []
    assert _get(sid).state == "awaiting_answer"


async def test_no_gaps_left_ends_with_done_and_info_message(uid):
    sid = _new_session(uid, profile=_profile(with_number=True))
    with SessionLocal() as s:
        for k in ("salary_expectation", "work_authorization", "availability", "relocation"):
            s.add(AnswerBankItem(user_id=uid, key=k, value="x"))
        s.commit()
    llm = FakeLLM()
    assert await _run(uid, llm) == 1
    assert llm.calls == []  # nothing to answer, nothing to ask
    iv = _get(sid)
    assert iv.state == "done"
    (m,) = _msgs(sid)
    assert (m.role, m.kind) == ("assistant", "info")


async def test_last_answer_still_yields_suggestion_when_no_gaps_left(uid):
    sid = _new_session(uid, state="awaiting_answer", asked_count=1, current_gap_id=GAP1,
                       profile=_profile())
    with SessionLocal() as s:
        for k in ("salary_expectation", "work_authorization", "availability", "relocation"):
            s.add(AnswerBankItem(user_id=uid, key=k, value="x"))
        s.commit()
    _answer(uid, sid, "Reduzi a latência em 30%")
    draft = SuggestionDraft(op="append_achievement", target_ref="e1",
                            text="Reduzi a latência em 30%")
    assert await _run(uid, FakeLLM(TurnResult(suggestion=draft))) == 1
    assert _get(sid).state == "done"
    assert len(_sugs(sid)) == 1 and _msgs(sid)[-1].kind == "info"


async def test_max_questions_ends_session(uid):
    sid = _new_session(uid, asked_count=2, max_questions=2)
    llm = FakeLLM()
    assert await _run(uid, llm) == 1
    assert _get(sid).state == "done" and llm.calls == []
    assert _msgs(sid)[-1].kind == "info"


def test_claim_skips_row_locked_by_another_session(uid):
    locked = _new_session(uid)
    with SessionLocal() as s:
        other_iv = InterviewSession(user_id=uid, profile_version=1)
        s.add(other_iv)
        s.commit()
        other = other_iv.id
    with SessionLocal() as holder:
        holder.execute(
            select(InterviewSession).where(InterviewSession.id == locked).with_for_update()
        ).scalar_one()
        assert ij._claim(SessionLocal, uid) == other
        assert ij._claim(SessionLocal, uid) is None
        holder.rollback()
    assert _get(locked).state == "generating"
    assert _get(other).state == "working"


async def test_invalid_llm_json_sets_error_and_retry_resumes_without_duplicate_question(uid):
    from rumo_core.interview.service import retry_interview

    sid = _new_session(uid)
    n = await _run(uid, FakeLLM(exc=LLMInvalidResponse("bad json")))
    iv = _get(sid)
    assert n == 1 and iv.state == "error" and iv.error and "bad json" not in iv.error
    assert _msgs(sid) == []
    with SessionLocal() as s:
        retry_interview(s, uid, sid)
    assert await _run(uid, FakeLLM(TurnResult(next_question="Qual resultado?"))) == 1
    iv = _get(sid)
    assert iv.state == "awaiting_answer" and iv.error is None and iv.asked_count == 1
    assert [m.kind for m in _msgs(sid)] == ["question"]


async def test_llm_error_sets_error_state(uid):
    sid = _new_session(uid)
    assert await _run(uid, FakeLLM(exc=LLMError("boom"))) == 1
    iv = _get(sid)
    assert iv.state == "error" and iv.error and "boom" not in iv.error


async def test_unexpected_error_generic_message_and_traceback_once(uid, caplog):
    sid = _new_session(uid)
    with caplog.at_level("ERROR"):
        assert await _run(uid, FakeLLM(exc=RuntimeError("kaboom"))) == 1
    iv = _get(sid)
    assert iv.state == "error" and iv.error == "Falha inesperada na entrevista."
    assert sum("unexpected failure" in r.message for r in caplog.records) == 1


async def test_cancelled_returns_to_generating_and_propagates(uid):
    sid = _new_session(uid)
    with pytest.raises(asyncio.CancelledError):
        await _run(uid, FakeLLM(exc=asyncio.CancelledError()))
    assert _get(sid).state == "generating"


async def test_rate_limited_returns_to_generating_and_pauses(uid, caplog):
    sid = _new_session(uid)
    llm = FakeLLM(exc=LLMRateLimited("slow"))
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    with caplog.at_level("WARNING"):
        assert await _run(uid, llm, clock=clock) == 0
        assert _get(sid).state == "generating" and len(llm.calls) == 1
        now[0] += 30
        assert await _run(uid, llm, clock=clock) == 0
        assert len(llm.calls) == 1
        assert sum("rate limited" in r.message for r in caplog.records) == 1
    now[0] += 31
    ok = FakeLLM(TurnResult(next_question="Q?"))
    assert await _run(uid, ok, clock=clock) == 1
    assert _get(sid).state == "awaiting_answer"


async def test_no_llm_key_leaves_generating_and_warns_once(uid, caplog):
    sid = _new_session(uid)
    with caplog.at_level("WARNING"):
        assert await _run(uid, None) == 0
        assert await _run(uid, None) == 0
    assert _get(sid).state == "generating"
    assert sum("GROQ_API_KEY" in r.message for r in caplog.records) == 1


async def test_finish_during_llm_call_is_not_overwritten(uid):
    sid = _new_session(uid)

    class FinishingLLM:
        async def complete_json(self, messages, schema):
            with SessionLocal() as s:
                s.get(InterviewSession, sid).state = "done"
                s.commit()
            return TurnResult(next_question="Q?")

    await _run(uid, FinishingLLM())
    assert _get(sid).state == "done" and _msgs(sid) == []


def test_startup_reset_working_to_generating(uid):
    sid = _new_session(uid, state="working")
    other = _new_session(uid, state="awaiting_answer")
    assert reset_working_sessions(SessionLocal, user_id=uid) == 1
    assert _get(sid).state == "generating"
    assert _get(other).state == "awaiting_answer"


async def test_user_scope_filter(uid):
    with SessionLocal() as s:
        u2 = User(email=f"worker-interview-{uuid.uuid4().hex}@example.test")
        s.add(u2)
        s.commit()
        uid2 = u2.id
    try:
        mine = _new_session(uid)
        theirs = _new_session(uid2)
        assert await _run(uid, FakeLLM(TurnResult(next_question="Q?"))) == 1
        assert _get(mine).state == "awaiting_answer"
        assert _get(theirs).state == "generating"
        assert await _run(uid, FakeLLM()) == 0
    finally:
        with SessionLocal() as s:
            for model in (ChatMessage, InterviewSession, ProfileChunk, Profile):
                s.execute(delete(model).where(model.user_id == uid2))
            s.execute(delete(User).where(User.id == uid2))
            s.commit()
