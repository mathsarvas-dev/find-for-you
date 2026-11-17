import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from rumo_core.config import Settings
from rumo_core.db import SessionLocal
from rumo_core.models import (
    AnswerBankItem, Application, ApplicationDocument, ApplicationQuestion,
    Company, Job, User,
)

pytestmark = pytest.mark.db


def _cleanup(s, uids):
    s.rollback()
    s.execute(delete(Application).where(Application.user_id.in_(uids)))
    s.execute(delete(AnswerBankItem).where(AnswerBankItem.user_id.in_(uids)))
    s.execute(delete(Job).where(Job.user_id.in_(uids)))
    s.execute(delete(Company).where(Company.user_id.in_(uids)))
    s.execute(delete(User).where(User.id.in_(uids)))
    s.commit()


@pytest.fixture
def session():
    with SessionLocal() as s:
        user = User(email=f"app-models-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        s.info["uid"] = user.id
        s.info["uids"] = [user.id]
        yield s
        _cleanup(s, s.info["uids"])


def _job(s, uid, ext="1"):
    c = Company(user_id=uid, source="greenhouse", identifier=f"acme{ext}", name="Acme")
    s.add(c)
    s.flush()
    j = Job(
        user_id=uid, company_id=c.id, external_id=ext,
        url="https://example.test/j/1", title="Engineer", description="d",
        raw={"id": 1}, content_hash="h", fingerprint="f",
    )
    s.add(j)
    s.commit()
    return j


def _app(uid, job_id, **kw):
    kw.setdefault("locale", "en")
    kw.setdefault("template", "us")
    return Application(user_id=uid, job_id=job_id, **kw)


def test_application_unique_per_user_job(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(_app(uid, j.id))
    session.commit()
    session.add(_app(uid, j.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_application_rejects_bad_state_and_template(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(_app(uid, j.id, state="x"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(_app(uid, j.id, template="fr"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_application_defaults(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    session.add(_app(uid, j.id))
    session.commit()
    a = session.execute(select(Application).where(Application.job_id == j.id)).scalar_one()
    assert a.state == "draft"
    assert a.attempts == 0
    assert a.tasks == []
    assert a.applied_at is None and a.referees is None


def test_documents_and_questions_cascade_with_job(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    a = _app(uid, j.id)
    session.add(a)
    session.flush()
    session.add(ApplicationDocument(
        user_id=uid, application_id=a.id, kind="resume", lines=[],
        status="valid", version=1,
    ))
    session.add(ApplicationQuestion(
        user_id=uid, application_id=a.id, position=1, source="pasted",
        label="Why?", kind="text", required=False, sensitive=False,
    ))
    session.commit()
    session.delete(j)
    session.commit()
    assert session.execute(select(Application)).all() == []
    assert session.execute(select(ApplicationDocument)).all() == []
    assert session.execute(select(ApplicationQuestion)).all() == []


def test_document_version_unique_per_kind(session):
    uid = session.info["uid"]
    j = _job(session, uid)
    a = _app(uid, j.id)
    session.add(a)
    session.flush()
    kw = dict(user_id=uid, application_id=a.id, kind="resume", lines=[], status="valid")
    session.add(ApplicationDocument(version=1, **kw))
    session.add(ApplicationDocument(version=2, **kw))
    session.commit()
    session.add(ApplicationDocument(version=2, **kw))
    with pytest.raises(IntegrityError):
        session.commit()


def test_answer_bank_lang_defaults_to_pt(session):
    uid = session.info["uid"]
    session.execute(
        AnswerBankItem.__table__.insert().values(
            user_id=uid, key="salary_expectation", value="R$ 8.000",
        )
    )
    session.commit()
    item = session.execute(
        select(AnswerBankItem).where(AnswerBankItem.user_id == uid)
    ).scalar_one()
    assert item.lang == "pt"


def test_settings_application_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
    s = Settings()
    assert s.application_daily_limit == 5
    assert s.application_max_attempts == 3
    assert s.application_call_timeout == 25
    assert s.greenhouse_questions_timeout == 10
