import threading
import uuid

import pytest
from sqlalchemy import delete, func, select

from rumo_core.db import SessionLocal
from rumo_core.interview.service import (
    InvalidSuggestion,
    NoProfile,
    NotFound,
    TargetMissing,
    WrongState,
    finish_interview,
    resolve_suggestion,
    retry_interview,
    skip_question,
    start_interview,
    submit_answer,
)
from rumo_core.interview.suggestions import SuggestionDraft
from rumo_core.models import (
    AnswerBankItem,
    ChatMessage,
    InterviewSession,
    Profile,
    ProfileChunk,
    ProfileSuggestion,
    User,
)
from rumo_core.profile.schema import CanonicalProfile, Experience, Skill
from rumo_core.profile.service import confirm_profile

pytestmark = pytest.mark.db

GAP = "achievement_without_number:e1"


def _profile(extra_exp=False):
    exps = [Experience(id="e1", title="Dev", company="Acme", start="2019",
                       description="Backend")]
    if extra_exp:
        exps.append(Experience(id="e2", title="QA", company="Beta", start="2017",
                               description="Testes"))
    return CanonicalProfile(experiences=exps, skills=[Skill(id="s1", name="Python")])


@pytest.fixture
def s():
    with SessionLocal() as sess:
        u = User(email=f"iv-service-{uuid.uuid4().hex}@example.test")
        sess.add(u)
        sess.commit()
        sess.info["uid"] = u.id
        yield sess
        sess.rollback()
        uid = u.id
        for model in (ProfileSuggestion, ChatMessage, AnswerBankItem, InterviewSession,
                      ProfileChunk, Profile):
            sess.execute(delete(model).where(model.user_id == uid))
        sess.execute(delete(User).where(User.id == uid))
        sess.commit()


def _confirmed(s, profile=None):
    return confirm_profile(s, s.info["uid"], profile or _profile(), narrative="n1")


def _awaiting(s, gap=GAP):
    """Interview session in awaiting_answer with a question asked."""
    _confirmed(s)
    iv = start_interview(s, s.info["uid"])
    iv.state = "awaiting_answer"
    iv.current_gap_id = gap
    s.add(ChatMessage(user_id=iv.user_id, session_id=iv.id, role="assistant",
                      kind="question", content="Q?", gap_id=gap))
    s.commit()
    return iv.id


def _suggestion(s, session_id, draft, answer="Reduzi a latência em 30%", gap=GAP):
    uid = s.info["uid"]
    msg = ChatMessage(user_id=uid, session_id=session_id, role="user", kind="answer",
                      content=answer, gap_id=gap)
    s.add(msg)
    s.flush()
    sug = ProfileSuggestion(user_id=uid, session_id=session_id, message_id=msg.id,
                            gap_id=gap, op=draft.op, payload=draft.model_dump())
    s.add(sug)
    s.commit()
    return sug.id


ACH = SuggestionDraft(op="append_achievement", target_ref="e1",
                      text="Reduzi a latência em 30%")


def _versions(s):
    s.rollback()
    return s.scalar(select(func.count()).select_from(Profile).where(
        Profile.user_id == s.info["uid"]))


def test_start_requires_confirmed_profile(s):
    with pytest.raises(NoProfile) as ei:
        start_interview(s, s.info["uid"])
    assert ei.value.message


def test_start_twice_returns_same_active_session(s):
    _confirmed(s)
    a = start_interview(s, s.info["uid"])
    b = start_interview(s, s.info["uid"])
    assert a.id == b.id and a.state == "generating" and a.profile_version == 1
    finish_interview(s, s.info["uid"], a.id)
    with pytest.raises(WrongState):
        finish_interview(s, s.info["uid"], a.id)
    c = start_interview(s, s.info["uid"])
    assert c.id != a.id


def test_submit_answer_wrong_state_409(s):
    _confirmed(s)
    iv = start_interview(s, s.info["uid"])
    with pytest.raises(WrongState):
        submit_answer(s, s.info["uid"], iv.id, "oi")


def test_submit_answer_stores_message_and_generating(s):
    sid = _awaiting(s)
    submit_answer(s, s.info["uid"], sid, "  Reduzi 30%  ")
    s.expire_all()
    iv = s.get(InterviewSession, sid)
    last = s.scalars(select(ChatMessage).where(ChatMessage.session_id == sid)
                     .order_by(ChatMessage.id.desc())).first()
    assert iv.state == "generating"
    assert (last.role, last.kind, last.content, last.gap_id) == (
        "user", "answer", "Reduzi 30%", GAP)


def test_submit_empty_or_huge_answer_rejected(s):
    sid = _awaiting(s)
    for bad in ("", "   ", "x" * 4001):
        with pytest.raises(ValueError):
            submit_answer(s, s.info["uid"], sid, bad)
    s.expire_all()
    assert s.get(InterviewSession, sid).state == "awaiting_answer"
    submit_answer(s, s.info["uid"], sid, "x" * 4000)  # boundary ok


def test_other_user_gets_not_found(s):
    sid = _awaiting(s)
    with pytest.raises(NotFound):
        submit_answer(s, s.info["uid"] + 10_000_000, sid, "oi")


def test_skip_marks_gap_handled(s):
    sid = _awaiting(s)
    skip_question(s, s.info["uid"], sid)
    s.expire_all()
    iv = s.get(InterviewSession, sid)
    last = s.scalars(select(ChatMessage).where(ChatMessage.session_id == sid)
                     .order_by(ChatMessage.id.desc())).first()
    assert iv.state == "generating" and iv.handled_gaps == [GAP]
    assert last.kind == "skip" and last.role == "user"
    with pytest.raises(WrongState):
        skip_question(s, s.info["uid"], sid)


def test_retry_only_from_error(s):
    sid = _awaiting(s)
    with pytest.raises(WrongState):
        retry_interview(s, s.info["uid"], sid)
    iv = s.get(InterviewSession, sid)
    iv.state, iv.error = "error", "falhou"
    s.commit()
    retry_interview(s, s.info["uid"], sid)
    s.expire_all()
    iv = s.get(InterviewSession, sid)
    assert iv.state == "generating" and iv.error is None


def test_resolve_accept_creates_new_version_with_note_and_reuses_embeddings(s):
    uid = s.info["uid"]
    p1 = confirm_profile(s, uid, _profile(extra_exp=True), narrative="n1")
    for c in s.scalars(select(ProfileChunk).where(
            ProfileChunk.user_id == uid, ProfileChunk.profile_version == p1.version)):
        c.embedding, c.model_version = [0.1] * 1024, "m1"
    s.commit()
    iv = start_interview(s, uid)
    iv.state, iv.current_gap_id = "awaiting_answer", GAP
    s.commit()
    sug_id = _suggestion(s, iv.id, ACH)

    out = resolve_suggestion(s, uid, sug_id, "accept", None)
    assert out.status == "accepted" and out.applied_version == 2
    latest = s.scalars(select(Profile).where(Profile.user_id == uid)
                       .order_by(Profile.version.desc())).first()
    assert latest.version == 2 and latest.narrative == "n1"
    exp = next(e for e in latest.data["experiences"] if e["id"] == "e1")
    assert [a["text"] for a in exp["achievements"]] == ["Reduzi a latência em 30%"]
    assert latest.data["notes"][-1]["topic"] == "achievement_without_number"
    assert "30%" in latest.data["notes"][-1]["text"]
    chunks = {c.source_ref: c for c in s.scalars(select(ProfileChunk).where(
        ProfileChunk.user_id == uid, ProfileChunk.profile_version == 2))}
    assert chunks["e2"].embedding is not None and chunks["e2"].model_version == "m1"
    assert chunks["e1"].embedding is None  # changed item re-embeds


def test_resolve_twice_is_wrong_state_and_one_version(s):
    uid = s.info["uid"]
    sid = _awaiting(s)
    sug_id = _suggestion(s, sid, ACH)
    resolve_suggestion(s, uid, sug_id, "accept", None)
    with pytest.raises(WrongState):
        resolve_suggestion(s, uid, sug_id, "accept", None)
    with pytest.raises(WrongState):
        resolve_suggestion(s, uid, sug_id, "reject", None)
    assert _versions(s) == 2


def test_resolve_concurrent_double_click_one_version(s):
    uid = s.info["uid"]
    sid = _awaiting(s)
    sug_id = _suggestion(s, sid, ACH)
    barrier = threading.Barrier(2)
    results: list = []

    def click():
        with SessionLocal() as other:
            barrier.wait()
            try:
                resolve_suggestion(other, uid, sug_id, "accept", None)
                results.append("ok")
            except WrongState:
                results.append("wrong_state")
            except Exception as e:  # pragma: no cover
                results.append(repr(e))

    threads = [threading.Thread(target=click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "wrong_state"]
    assert _versions(s) == 2


def test_resolve_target_removed_raises_target_missing_no_version(s):
    uid = s.info["uid"]
    sid = _awaiting(s)
    sug_id = _suggestion(s, sid, ACH)
    # a newer version dropped experience e1
    confirm_profile(s, uid, CanonicalProfile(
        experiences=[Experience(id="e9", title="X", start="2020", description="d")]),
        narrative="n1")
    with pytest.raises(TargetMissing) as ei:
        resolve_suggestion(s, uid, sug_id, "accept", None)
    assert ei.value.message
    assert _versions(s) == 2
    s.expire_all()
    assert s.get(ProfileSuggestion, sug_id).status == "pending"


def test_resolve_adjust_revalidated_against_answer(s):
    uid = s.info["uid"]
    sid = _awaiting(s)
    sug_id = _suggestion(s, sid, ACH)
    bad = SuggestionDraft(op="append_achievement", target_ref="e1",
                          text="Reduzi a latência em 45%")
    with pytest.raises(InvalidSuggestion) as ei:
        resolve_suggestion(s, uid, sug_id, "adjust", bad)
    assert "número" in ei.value.message
    assert _versions(s) == 1
    s.expire_all()
    assert s.get(ProfileSuggestion, sug_id).status == "pending"
    good = SuggestionDraft(op="append_achievement", target_ref="e1",
                           text="Reduzi a latência em 30% no backend")
    out = resolve_suggestion(s, uid, sug_id, "adjust", good)
    assert out.status == "adjusted" and out.applied_version == 2
    latest = s.scalars(select(Profile).where(Profile.user_id == uid)
                       .order_by(Profile.version.desc())).first()
    assert latest.data["experiences"][0]["achievements"][0]["text"].endswith("backend")


def test_resolve_preference_goes_to_answer_bank_no_version(s):
    uid = s.info["uid"]
    gap = "pref_salary_expectation:-"
    sid = _awaiting(s, gap=gap)
    draft = SuggestionDraft(op="set_preference", key="salary_expectation", value="8000")
    sug_id = _suggestion(s, sid, draft, answer="Quero 8000", gap=gap)
    out = resolve_suggestion(s, uid, sug_id, "accept", None)
    assert out.status == "accepted" and out.applied_version is None
    item = s.scalars(select(AnswerBankItem).where(AnswerBankItem.user_id == uid)).one()
    assert (item.key, item.value, item.source_message_id) == (
        "salary_expectation", "8000", out.message_id)
    assert _versions(s) == 1
    # upsert: a later preference answer overwrites, no duplicate key
    sug2 = _suggestion(s, sid, SuggestionDraft(
        op="set_preference", key="salary_expectation", value="9000"),
        answer="Agora 9000", gap=gap)
    resolve_suggestion(s, uid, sug2, "accept", None)
    s.expire_all()
    item = s.scalars(select(AnswerBankItem).where(AnswerBankItem.user_id == uid)).one()
    assert item.value == "9000"


def test_resolve_reject_marks_rejected(s):
    uid = s.info["uid"]
    sug_id = _suggestion(s, _awaiting(s), ACH)
    out = resolve_suggestion(s, uid, sug_id, "reject", None)
    assert out.status == "rejected" and _versions(s) == 1


def test_resolve_applies_to_latest_profile_not_session_version(s):
    uid = s.info["uid"]
    sid = _awaiting(s)  # session built on version 1
    sug_id = _suggestion(s, sid, ACH)
    newer = _profile()
    newer.skills.append(Skill(id="s2", name="Go"))
    confirm_profile(s, uid, newer, narrative="n1")  # version 2
    out = resolve_suggestion(s, uid, sug_id, "accept", None)
    assert out.applied_version == 3
    latest = s.scalars(select(Profile).where(Profile.user_id == uid)
                       .order_by(Profile.version.desc())).first()
    assert {x["name"] for x in latest.data["skills"]} == {"Python", "Go"}
    assert latest.data["experiences"][0]["achievements"]


def _two_suggestions(s):
    uid = s.info["uid"]
    sid = _awaiting(s)
    a = _suggestion(s, sid, ACH)
    d2 = SuggestionDraft(op="append_achievement", target_ref="e1", text="Cortei custos em 20%")
    b = _suggestion(s, sid, d2, answer="Cortei custos em 20%")
    return uid, a, b


def _parallel(fns):
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def run(i, fn):
        with SessionLocal() as other:
            barrier.wait()
            try:
                results[i] = fn(other)
            except Exception as e:  # noqa: BLE001
                results[i] = e

    ts = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


def _latest(s):
    s.rollback()
    s.expire_all()
    return s.scalars(select(Profile).where(Profile.user_id == s.info["uid"])
                     .order_by(Profile.version.desc())).first()


def _achievements(profile_row):
    return {a["text"] for e in profile_row.data["experiences"] for a in e["achievements"]}


def test_two_different_suggestions_concurrently_no_lost_update(s):
    uid, a, b = _two_suggestions(s)
    res = _parallel([
        lambda o: resolve_suggestion(o, uid, a, "accept", None),
        lambda o: resolve_suggestion(o, uid, b, "accept", None),
    ])
    assert all(not isinstance(r, Exception) for r in res), res
    latest = _latest(s)
    assert latest.version == 3
    assert _achievements(latest) == {"Reduzi a latência em 30%", "Cortei custos em 20%"}
    sugs = [s.get(ProfileSuggestion, i) for i in (a, b)]
    assert {x.status for x in sugs} == {"accepted"}
    assert sorted(x.applied_version for x in sugs) == [2, 3]


def test_resolve_concurrent_with_direct_confirm_no_lost_update(s):
    uid = s.info["uid"]
    sug = _suggestion(s, _awaiting(s), ACH)

    def edit(o):
        p = _profile()
        p.skills.append(Skill(id="s2", name="Go"))
        return confirm_profile(o, uid, p, narrative="n1")

    res = _parallel([lambda o: resolve_suggestion(o, uid, sug, "accept", None), edit])
    assert all(not isinstance(r, Exception) for r in res), res
    assert s.get(ProfileSuggestion, sug).status == "accepted"
    latest = _latest(s)
    # the resolve is applied on top of the latest version at that moment, so it is
    # never lost; the edit only wins/loses by ordering, never silently overwrites it
    assert _achievements(latest) == {"Reduzi a latência em 30%"}


def test_resolve_after_concurrent_edit_keeps_both_changes(s):
    """Deterministic interleave: an edit holds the profile lock while resolve waits."""
    uid = s.info["uid"]
    sug = _suggestion(s, _awaiting(s), ACH)
    from rumo_core.profile.locks import lock_user_profile

    holder = SessionLocal()
    lock_user_profile(holder, uid)  # a profile write is "in progress"
    done = []

    def resolver():
        with SessionLocal() as o:
            done.append(resolve_suggestion(o, uid, sug, "accept", None).status)

    t = threading.Thread(target=resolver)
    t.start()
    t.join(0.5)
    assert t.is_alive()  # blocked behind the profile lock
    p = _profile()
    p.skills.append(Skill(id="s2", name="Go"))
    confirm_profile(holder, uid, p, narrative="n1")  # commits, releases lock
    holder.close()
    t.join(10)
    assert done == ["accepted"]
    latest = _latest(s)
    assert latest.version == 3
    assert {x["name"] for x in latest.data["skills"]} == {"Python", "Go"}
    assert _achievements(latest) == {"Reduzi a latência em 30%"}


def test_concurrent_start_returns_same_session(s):
    uid = s.info["uid"]
    _confirmed(s)
    res = _parallel([lambda o: start_interview(o, uid).id for _ in range(4)])
    assert all(isinstance(r, int) for r in res), res
    assert len(set(res)) == 1
    s.rollback()
    assert s.scalar(select(func.count()).select_from(InterviewSession).where(
        InterviewSession.user_id == uid)) == 1


def test_concurrent_set_preference_upserts_single_row(s):
    uid = s.info["uid"]
    gap = "pref_salary_expectation:-"
    sid = _awaiting(s, gap=gap)
    ids = [
        _suggestion(s, sid, SuggestionDraft(op="set_preference", key="salary_expectation",
                                            value=v), answer=f"Quero {v}", gap=gap)
        for v in ("8000", "9000")
    ]
    res = _parallel([lambda o, i=i: resolve_suggestion(o, uid, i, "accept", None)
                     for i in ids])
    assert all(not isinstance(r, Exception) for r in res), res
    s.rollback()
    items = s.scalars(select(AnswerBankItem).where(AnswerBankItem.user_id == uid)).all()
    assert len(items) == 1 and items[0].value in ("8000", "9000")
