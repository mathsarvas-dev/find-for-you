import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from rumo_core.db import SessionLocal
from rumo_core.models import (
    AnswerBankItem, ChatMessage, InterviewSession, ProfileSuggestion, User,
)

pytestmark = pytest.mark.db


def _cleanup(s, uids):
    s.rollback()
    for model in (ProfileSuggestion, ChatMessage, AnswerBankItem, InterviewSession):
        s.execute(delete(model).where(model.user_id.in_(uids)))
    s.execute(delete(User).where(User.id.in_(uids)))
    s.commit()


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"interview-models-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        s.info["uids"] = [user.id]
        yield s
        _cleanup(s, s.info["uids"])


def _session_row(uid):
    return InterviewSession(user_id=uid, profile_version=1)


def _seed(s, uid):
    sess = _session_row(uid)
    s.add(sess)
    s.flush()
    q = ChatMessage(
        user_id=uid, session_id=sess.id, role="assistant", kind="question",
        content="Qual sua stack?", gap_id="gap-1",
    )
    a = ChatMessage(
        user_id=uid, session_id=sess.id, role="user", kind="answer",
        content="Python", gap_id="gap-1",
    )
    s.add_all([q, a])
    s.flush()
    sug = ProfileSuggestion(
        user_id=uid, session_id=sess.id, message_id=a.id, gap_id="gap-1",
        op="add_skill", payload={"name": "Python"},
    )
    s.add(sug)
    s.commit()
    return sess.id


def test_interview_tables_roundtrip(session):
    uid = session.info["uid"]
    sid = _seed(session, uid)
    session.add(AnswerBankItem(user_id=uid, key="salary", value="10k"))
    session.commit()
    session.expire_all()

    sess = session.scalars(
        select(InterviewSession).where(InterviewSession.user_id == uid)
    ).one()
    assert sess.id == sid
    assert sess.state == "generating"
    assert sess.asked_count == 0
    assert sess.max_questions == 12
    assert sess.handled_gaps == []
    assert sess.current_gap_id is None and sess.error is None
    assert sess.created_at is not None and sess.updated_at is not None

    msgs = session.scalars(
        select(ChatMessage).where(ChatMessage.user_id == uid).order_by(ChatMessage.id)
    ).all()
    assert [m.role for m in msgs] == ["assistant", "user"]
    assert all(m.channel == "interview" for m in msgs)

    sug = session.scalars(
        select(ProfileSuggestion).where(ProfileSuggestion.user_id == uid)
    ).one()
    assert sug.status == "pending"
    assert sug.payload == {"name": "Python"}
    assert sug.applied_version is None
    assert sug.message_id == msgs[1].id

    item = session.scalars(
        select(AnswerBankItem).where(AnswerBankItem.user_id == uid)
    ).one()
    assert item.value == "10k" and item.source_message_id is None


def test_answer_bank_unique_per_user_key(session):
    uid = session.info["uid"]
    session.add(AnswerBankItem(user_id=uid, key="k", value="a"))
    session.commit()
    session.add(AnswerBankItem(user_id=uid, key="k", value="b"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    other = User(email=f"interview-models-{uuid.uuid4().hex}@example.test")
    session.add(other)
    session.commit()
    session.info["uids"].append(other.id)
    session.add(AnswerBankItem(user_id=other.id, key="k", value="c"))
    session.commit()


def test_cascade_delete_session_removes_messages_and_suggestions(session):
    uid = session.info["uid"]
    sid = _seed(session, uid)
    session.delete(session.get(InterviewSession, sid))
    session.commit()
    session.expire_all()
    assert session.scalars(select(ChatMessage).where(ChatMessage.user_id == uid)).all() == []
    assert session.scalars(
        select(ProfileSuggestion).where(ProfileSuggestion.user_id == uid)
    ).all() == []
