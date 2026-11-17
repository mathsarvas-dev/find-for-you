import asyncio
import logging
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, update

from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.llm.base import LLMError, LLMRateLimited
from rumo_core.models import (
    Application, ApplicationDocument, Company, Job, Profile, User,
)
from rumo_core.profile.schema import Achievement, Basics, CanonicalProfile, Experience, Skill
from worker import prepare_applications as pa
from worker.prepare_applications import (
    MSG_DRAFT_INVALID,
    MSG_LLM_FAIL,
    MSG_NO_PROFILE,
    prepare_applications,
)

SECRET = "SECRET-POSTING-TEXT-XYZ"


def _profile_data(version_tag="v1") -> dict:
    return CanonicalProfile(
        basics=Basics(name="Ana Souza", email="ana@pessoal.example", headline="Engenheira de Dados"),
        summary="Engenheira de dados com foco em pipelines.",
        experiences=[Experience(
            id="exp1", company="Acme", title="Engenheira de Dados", start="2019-03", end="2023-08",
            description="Pipelines em Python e Airflow.",
            achievements=[Achievement(id="ach1", text=f"Reduzi o tempo do pipeline em 40% com Airflow {version_tag}")],
        )],
        skills=[Skill(id="sk1", name="Python"), Skill(id="sk2", name="SQL")],
    ).model_dump(mode="json")


def _req_answer():
    return {"must": ["Airflow"], "nice": [], "keywords": []}


def _draft_answer(text="Reduzi o tempo do pipeline em 40% com Airflow"):
    return {
        "summary": {"text": "Engenheira de dados com foco em Airflow.", "source_refs": ["summary"], "covers": ["m1"]},
        "experiences": [{"item_id": "exp1", "bullets": [
            {"text": text, "source_refs": ["ach1"], "covers": ["m1"]}]}],
        "projects": [], "skill_ids": ["sk1"],
    }


class FakeLLM:
    """Answers in queue order: requirements first, then the draft(s)."""

    def __init__(self, *answers, exc=None, hook=None, delay=0.0):
        self.answers = list(answers)
        self.exc = exc
        self.hook = hook
        self.delay = delay
        self.calls = []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        if self.hook:
            self.hook(len(self.calls))
        await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        answer = self.answers.pop(0) if self.answers else _draft_answer()
        return schema.model_validate(answer)


class FakeEmbedder:
    dimensions = 1024
    model_version = MODEL_VERSION

    def __init__(self, fail=False):
        self.fail = fail

    def embed(self, texts):
        if self.fail:
            raise OSError("model unavailable")
        return [[1.0] + [0.0] * 1023 for _ in texts]


@pytest.fixture(autouse=True)
def _state():
    pa._reset_state()
    yield
    pa._reset_state()


@pytest.fixture
def make_user():
    created: list[int] = []

    def new_user(profile=True):
        with SessionLocal() as s:
            u = User(email=f"prep-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            s.add(Company(user_id=u.id, source="fake", identifier=f"co{u.id}", name="Globex"))
            if profile:
                s.add(Profile(user_id=u.id, version=1, data=_profile_data(), content_hash="p1"))
            s.commit()
            return u.id

    yield new_user
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(ApplicationDocument).where(ApplicationDocument.user_id == uid))
            s.execute(delete(Application).where(Application.user_id == uid))
            s.execute(delete(AnswerBankItem).where(AnswerBankItem.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


@pytest.fixture
def uid(make_user):
    return make_user()


def _generating(uid, i=0, *, job_hash="h0", **job_kw):
    """Open job + application in 'generating'; returns the application id."""
    with SessionLocal() as s:
        c = s.execute(select(Company).where(Company.user_id == uid)).scalars().one()
        fields = dict(
            user_id=uid, company_id=c.id, external_id=f"e{i}",
            url=f"https://example.test/{i}", title="Data Engineer", location_text="Remoto",
            description=f"Buscamos engenheira com Airflow. {SECRET}",
            raw={}, content_hash=job_hash, fingerprint=f"f{i}", status="open", country="US",
            language="en",
        )
        fields.update(job_kw)
        j = Job(**fields)
        s.add(j)
        s.flush()
        a = Application(user_id=uid, job_id=j.id, state="generating", locale="en",
                        template="us", job_content_hash=job_hash)
        s.add(a)
        s.commit()
        return a.id


def _get(app_id) -> Application:
    with SessionLocal() as s:
        a = s.get(Application, app_id)
        s.expunge(a)
        return a


def _docs(app_id, kind="resume"):
    with SessionLocal() as s:
        rows = s.execute(select(ApplicationDocument).where(
            ApplicationDocument.application_id == app_id, ApplicationDocument.kind == kind)).scalars().all()
        for r in rows:
            s.expunge(r)
        return rows


async def _run(uid=None, llm=None, embedder=None, max_attempts=3, daily_limit=None, call_timeout=25.0, **kw):
    return await prepare_applications(
        SessionLocal, llm, embedder or FakeEmbedder(), max_attempts=max_attempts,
        daily_limit=daily_limit, call_timeout=call_timeout, user_id=uid, **kw)


@pytest.mark.db
async def test_generating_to_ready_with_document_and_coverage(uid):
    a = _generating(uid)
    llm = FakeLLM(_req_answer(), _draft_answer())
    assert await _run(uid, llm) == 1
    app = _get(a)
    assert app.state == "ready" and app.error is None and app.attempts == 0
    assert app.profile_version == 1 and app.job_content_hash == "h0"
    assert app.requirements["must"][0]["text"] == "Airflow"
    assert app.coverage["must"] == {"covered": 1, "total": 1}
    (doc,) = _docs(a)
    assert doc.version == 1 and doc.status == "valid"
    assert any(line["section"] == "bullet" for line in doc.lines)


@pytest.mark.db
async def test_requirements_reused_when_job_unchanged(uid):
    a = _generating(uid)
    llm = FakeLLM(_req_answer(), _draft_answer())
    await _run(uid, llm)
    with SessionLocal() as s:
        s.execute(update(Application).where(Application.id == a).values(state="generating"))
        s.commit()
    llm2 = FakeLLM(_draft_answer())
    await _run(uid, llm2)
    assert len(llm.calls) == 2 and len(llm2.calls) == 1  # requirement call skipped the 2nd time
    assert _docs(a)[-1].version == 2


@pytest.mark.db
async def test_requirements_reextracted_when_job_hash_changed(uid):
    a = _generating(uid)
    await _run(uid, FakeLLM(_req_answer(), _draft_answer()))
    with SessionLocal() as s:
        s.execute(update(Job).where(Job.user_id == uid).values(content_hash="h9"))
        s.execute(update(Application).where(Application.id == a).values(state="generating"))
        s.commit()
    llm2 = FakeLLM(_req_answer(), _draft_answer())
    await _run(uid, llm2)
    assert len(llm2.calls) == 2


@pytest.mark.db
async def test_draft_invalid_sets_error_without_document(uid):
    a = _generating(uid)
    bad = _draft_answer("Reduzi o tempo do pipeline em 60% com Airflow")
    llm = FakeLLM(_req_answer(), bad, bad)
    await _run(uid, llm)
    app = _get(a)
    assert app.state == "error" and app.error == MSG_DRAFT_INVALID
    assert _docs(a) == []


@pytest.mark.db
async def test_llm_error_counts_attempt_then_error_at_max(uid):
    a = _generating(uid)
    await _run(uid, FakeLLM(exc=LLMError("boom")), max_attempts=2)
    assert _get(a).state == "generating" and _get(a).attempts == 1
    await _run(uid, FakeLLM(exc=LLMError("boom")), max_attempts=2)
    app = _get(a)
    assert app.state == "error" and app.error == MSG_LLM_FAIL and app.attempts == 2


@pytest.mark.db
async def test_rate_limit_pauses_without_attempt(uid, caplog):
    a = _generating(uid)
    clock = [100.0]
    await _run(uid, FakeLLM(exc=LLMRateLimited("429")), clock=lambda: clock[0])
    app = _get(a)
    assert app.state == "generating" and app.attempts == 0
    assert pa._pause_until == 160.0
    # while paused nothing is processed
    await _run(uid, FakeLLM(_req_answer(), _draft_answer()), clock=lambda: 120.0)
    assert _get(a).state == "generating"


@pytest.mark.db
async def test_timeout_pauses_without_attempt(uid):
    a = _generating(uid)
    await _run(uid, FakeLLM(delay=0.5), call_timeout=0.05)
    assert _get(a).state == "generating" and _get(a).attempts == 0


@pytest.mark.db
async def test_daily_limit_blocks_generation(uid):
    a = _generating(uid)
    with SessionLocal() as s:
        for v in range(1, 6):
            s.add(ApplicationDocument(
                user_id=uid, application_id=_generating(uid, i=100 + v), kind="resume",
                lines=[], status="valid", version=1))
        s.commit()
    llm = FakeLLM(_req_answer(), _draft_answer())
    assert await _run(uid, llm, daily_limit=5) == 0
    assert llm.calls == [] and _get(a).state == "generating"


@pytest.mark.db
async def test_interview_gate_pauses(uid):
    from rumo_core.models import InterviewSession

    _generating(uid)
    with SessionLocal() as s:
        s.add(InterviewSession(user_id=uid, profile_version=1, state="generating"))
        s.commit()
    llm = FakeLLM(_req_answer(), _draft_answer())
    try:
        assert await _run(uid, llm) == 0
        assert llm.calls == []
    finally:
        with SessionLocal() as s:
            s.execute(delete(InterviewSession).where(InterviewSession.user_id == uid))
            s.commit()


async def test_no_llm_does_nothing(caplog):
    with caplog.at_level(logging.WARNING):
        assert await prepare_applications(SessionLocal, None, FakeEmbedder(), max_attempts=3, daily_limit=None) == 0
    assert any("disabled" in r.message for r in caplog.records)


@pytest.mark.db
async def test_two_workers_do_not_take_same_application(uid):
    a = _generating(uid)
    llm = FakeLLM(_req_answer(), _draft_answer(), delay=0.05)
    results = await asyncio.gather(_run(uid, llm), _run(uid, llm))
    assert sorted(results) == [0, 1]
    assert len(_docs(a)) == 1
    assert len(llm.calls) == 2  # one requirements + one draft, not two of each


@pytest.mark.db
async def test_new_profile_version_during_generation_discards_and_requeues(uid):
    a = _generating(uid)

    def bump_profile(n):
        if n == 2:  # during the draft call: a new confirmed version appears
            with SessionLocal() as s:
                s.add(Profile(user_id=uid, version=2, data=_profile_data("v2"), content_hash="p2"))
                s.commit()

    await _run(uid, FakeLLM(_req_answer(), _draft_answer(), hook=bump_profile))
    app = _get(a)
    assert app.state == "generating" and _docs(a) == []


@pytest.mark.db
async def test_network_failure_of_embedder_falls_back_to_all_experiences(uid):
    a = _generating(uid)
    llm = FakeLLM(_req_answer(), _draft_answer())
    await _run(uid, llm, embedder=FakeEmbedder(fail=True))
    assert _get(a).state == "ready"
    draft_prompt = " ".join(m["content"] for m in llm.calls[1])
    assert "Reduzi o tempo do pipeline" in draft_prompt


@pytest.mark.db
async def test_no_profile_sets_error(make_user):
    uid = make_user(profile=False)
    a = _generating(uid)
    await _run(uid, FakeLLM(_req_answer(), _draft_answer()))
    app = _get(a)
    assert app.state == "error" and app.error == MSG_NO_PROFILE


@pytest.mark.db
async def test_logs_have_no_job_or_llm_text(uid, caplog):
    _generating(uid)
    with caplog.at_level(logging.DEBUG):
        await _run(uid, FakeLLM(exc=LLMError(SECRET)))
    assert SECRET not in caplog.text


@pytest.mark.db
async def test_cover_decisions_preserved_in_coverage(uid):
    a = _generating(uid)
    await _run(uid, FakeLLM(_req_answer(), _draft_answer()))
    with SessionLocal() as s:
        app = s.get(Application, a)
        app.coverage = {**app.coverage, "items": [{**i, "decision": "lack"} for i in app.coverage["items"]]}
        app.state = "generating"
        s.commit()
    await _run(uid, FakeLLM(_draft_answer()))
    cov = _get(a).coverage
    assert cov["items"][0]["decision"] == "lack"


def test_generated_last_24h_counts_resume_only(uid):
    from rumo_core.applications.quota import generated_last_24h

    a = _generating(uid)
    with SessionLocal() as s:
        s.add(ApplicationDocument(user_id=uid, application_id=a, kind="resume", lines=[], status="valid", version=1))
        s.add(ApplicationDocument(user_id=uid, application_id=a, kind="cover_letter", lines=[], status="valid", version=1))
        s.commit()
        before = generated_last_24h(s)
        s.execute(update(ApplicationDocument).where(
            ApplicationDocument.application_id == a, ApplicationDocument.kind == "resume")
            .values(created_at=func.now() - timedelta(hours=25)))
        s.commit()
        assert generated_last_24h(s) == before - 1


def test_registered_after_interview_turns():
    from rumo_core.config import Settings
    from worker.main import build_registry

    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/x")
    names = list(build_registry(s, object(), object(), object())._jobs)
    assert names.index("prepare_applications") == names.index("interview_turns") + 1


# ---- answer drafts (queued by the person) ----------------------------------

from rumo_core.models import AnswerBankItem, ApplicationQuestion  # noqa: E402
from worker.prepare_applications import MSG_ANSWER_INVALID  # noqa: E402


def _ready_with_question(uid, *, label="Por que nós?", answer_state="empty", sensitive=False, i=50):
    """A ready application with a resume document and one question with a queued draft."""
    app_id = _generating(uid, i=i)
    with SessionLocal() as s:
        a = s.get(Application, app_id)
        a.state = "ready"
        a.profile_version = 1  # a ready application always has the profile version it was written on
        s.add(ApplicationDocument(user_id=uid, application_id=app_id, kind="resume", lines=[], status="valid", version=1))
        q = ApplicationQuestion(user_id=uid, application_id=app_id, position=1, source="pasted", label=label,
                                kind="text", required=False, sensitive=sensitive, answer_state=answer_state)
        s.add(q)
        s.flush()
        a.tasks = [{"kind": "answer", "qid": q.id, "state": "queued", "error": None}]
        s.commit()
        return app_id, q.id


def _question(qid) -> ApplicationQuestion:
    with SessionLocal() as s:
        q = s.get(ApplicationQuestion, qid)
        s.expunge(q)
        return q


@pytest.mark.db
async def test_answer_task_drafted_and_removed(uid):
    app_id, qid = _ready_with_question(uid)
    llm = FakeLLM({"text": "Reduzi o tempo do pipeline em 40% com Airflow", "source_refs": ["ach1"]})
    assert await _run(uid, llm) == 1
    q = _question(qid)
    assert q.answer_state == "draft" and "40%" in q.answer
    assert _get(app_id).tasks == []


@pytest.mark.db
async def test_answer_task_failed_message(uid):
    app_id, qid = _ready_with_question(uid)
    bad = {"text": "Trabalhei 10 anos na Google", "source_refs": ["ach1"]}
    await _run(uid, FakeLLM(bad, bad))
    app = _get(app_id)
    (task,) = app.tasks
    assert task["state"] == "failed" and task["error"] == MSG_ANSWER_INVALID
    assert _question(qid).answer is None


@pytest.mark.db
async def test_user_edit_not_overwritten(uid):
    app_id, qid = _ready_with_question(uid, answer_state="edited")
    with SessionLocal() as s:
        s.execute(update(ApplicationQuestion).where(ApplicationQuestion.id == qid).values(answer="Minha resposta"))
        s.commit()
    await _run(uid, FakeLLM({"text": "Reduzi o tempo do pipeline em 40% com Airflow", "source_refs": ["ach1"]}))
    q = _question(qid)
    assert q.answer == "Minha resposta" and q.answer_state == "edited"
    assert _get(app_id).tasks == []


@pytest.mark.db
async def test_generation_has_priority_over_tasks(uid):
    ready_id, _ = _ready_with_question(uid, i=60)
    gen_id = _generating(uid, i=61)
    llm = FakeLLM(_req_answer(), _draft_answer())
    await _run(uid, llm)
    assert _get(gen_id).state == "ready"
    assert len(llm.calls) == 2  # requirements + resume draft: the answer waits for the next tick
    assert len(_get(ready_id).tasks) == 1


@pytest.mark.db
async def test_bank_of_other_user_never_sent_to_llm(uid, make_user):
    other = make_user()
    with SessionLocal() as s:
        s.add(AnswerBankItem(user_id=other, key="why_us", value="Segredo do outro usuário", lang="pt"))
        s.commit()
    _, qid = _ready_with_question(uid, i=70)
    llm = FakeLLM({"text": "Reduzi o tempo do pipeline em 40% com Airflow", "source_refs": ["ach1"]})
    await _run(uid, llm)
    blob = " ".join(m["content"] for c in llm.calls for m in c)
    assert "Segredo do outro usuário" not in blob


# ---- cover letter task ------------------------------------------------------

from rumo_core.models import ApplicationDocument as _AppDoc  # noqa: E402


def _ready_with_letter_task(uid, i=80):
    app_id = _generating(uid, i=i)
    with SessionLocal() as s:
        a = s.get(Application, app_id)
        a.state = "ready"
        a.profile_version = 1
        a.requirements = {"must": [{"id": "m1", "text": "Airflow"}], "nice": [], "keywords": []}
        s.add(_AppDoc(user_id=uid, application_id=app_id, kind="resume", lines=[], status="valid", version=1))
        a.tasks = [{"kind": "cover_letter", "state": "queued", "error": None}]
        s.commit()
    return app_id


@pytest.mark.db
async def test_letter_task_creates_document(uid):
    app_id = _ready_with_letter_task(uid)
    llm = FakeLLM({"paragraphs": [{"text": "Reduzi o tempo do pipeline em 40% com Airflow",
                                   "source_refs": ["ach1"], "covers": ["m1"]}]})
    assert await _run(uid, llm) == 1
    with SessionLocal() as s:
        letters = s.execute(select(_AppDoc).where(_AppDoc.application_id == app_id,
                                                  _AppDoc.kind == "cover_letter")).scalars().all()
    assert len(letters) == 1 and letters[0].version == 1 and letters[0].status == "valid"
    assert _get(app_id).tasks == []


@pytest.mark.db
async def test_letter_task_failure_keeps_no_document(uid):
    app_id = _ready_with_letter_task(uid, i=81)
    bad = {"paragraphs": [{"text": "Reduzi em 60% o pipeline", "source_refs": ["ach1"], "covers": []}]}
    await _run(uid, FakeLLM(bad, bad))
    (task,) = _get(app_id).tasks
    assert task["state"] == "failed" and task["error"] == MSG_DRAFT_INVALID
    with SessionLocal() as s:
        assert s.execute(select(_AppDoc).where(_AppDoc.application_id == app_id,
                                               _AppDoc.kind == "cover_letter")).first() is None
