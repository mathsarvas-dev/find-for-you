import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

import api.applications as api_apps
from api.main import app, current_user_id
from rumo_core.config import get_settings
from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.models import (
    AnswerBankItem, Application, ApplicationDocument, Company, Job, Profile, ProfileChunk, User,
)
from rumo_core.profile.schema import Achievement, Basics, CanonicalProfile, Experience, Skill

DIM = 1024


def _profile_data() -> dict:
    return CanonicalProfile(
        basics=Basics(name="Ana Souza", email="ana@pessoal.example", phone="+55 11 98765-4321"),
        summary="Engenheira de dados com foco em pipelines.",
        experiences=[Experience(
            id="exp1", company="Acme", title="Engenheira de Dados", start="2019-03", end="2023-08",
            description="Pipelines em Airflow.",
            achievements=[Achievement(id="ach1", text="Reduzi o tempo do pipeline em 40% com Airflow")],
        )],
        skills=[Skill(id="sk1", name="Python"), Skill(id="sk2", name="SQL")],
    ).model_dump(mode="json")


def _vec(i: int = 0) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


@pytest.fixture
def env():
    created: list[int] = []

    def new_user(*, profile=True, vectorized=True, version=1):
        with SessionLocal() as s:
            u = User(email=f"api-apps-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            s.add(Company(user_id=u.id, source="greenhouse", identifier=f"acme{u.id}", name="Acme"))
            if profile:
                s.add(Profile(user_id=u.id, version=version, data=_profile_data(), content_hash="p"))
                s.add(ProfileChunk(
                    user_id=u.id, profile_version=version, source_ref="exp1", kind="experience",
                    text="t", content_hash="h", embedding=_vec() if vectorized else None,
                    model_version=MODEL_VERSION if vectorized else None,
                ))
            s.commit()
            return u.id

    def add_job(uid, **kw):
        with SessionLocal() as s:
            c = s.execute(select(Company).where(Company.user_id == uid)).scalars().one()
            fields = dict(
                user_id=uid, company_id=c.id, external_id=uuid.uuid4().hex[:8],
                url="https://example.test/j", title="Data Engineer", location_text="Remoto",
                description="Vaga secreta com Kubernetes e Terraform.",
                raw={}, content_hash="h0", fingerprint=uuid.uuid4().hex, status="open",
                language="en", country="US",
            )
            fields.update(kw)
            j = Job(**fields)
            s.add(j)
            s.commit()
            return j.id

    def add_application(uid, job_id, *, state="ready", lines=None, profile_version=1,
                        job_hash="h0", template="us", locale="en"):
        with SessionLocal() as s:
            a = Application(user_id=uid, job_id=job_id, state=state, locale=locale,
                            template=template, profile_version=profile_version,
                            job_content_hash=job_hash, requirements={"must": [], "nice": [], "keywords": []},
                            coverage={"must": {"covered": 0, "total": 0}, "nice": {"covered": 0, "total": 0}, "items": []})
            s.add(a)
            s.flush()
            if lines is not None:
                s.add(ApplicationDocument(
                    user_id=uid, application_id=a.id, kind="resume", lines=lines,
                    status="needs_fix" if any(x.get("violations") for x in lines) else "valid",
                    version=1,
                ))
            s.commit()
            return a.id

    def client_as(uid):
        app.dependency_overrides[current_user_id] = lambda: uid
        return TestClient(app)

    yield new_user, add_job, add_application, client_as
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(ApplicationDocument).where(ApplicationDocument.user_id == uid))
            s.execute(delete(Application).where(Application.user_id == uid))
            s.execute(delete(AnswerBankItem).where(AnswerBankItem.user_id == uid))
            s.execute(delete(ProfileChunk).where(ProfileChunk.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


def _fact(lid="L1", text="Engenheira de Dados — Acme", ref="exp1"):
    return {"id": lid, "section": "experience", "text": text, "fields": {"company": "Acme"},
            "parent": None, "source_refs": [ref], "origin": "fact", "covers": [], "violations": []}


def _ai(lid="L2", text="Reduzi o tempo do pipeline em 40% com Airflow", refs=("ach1",), violations=None):
    return {"id": lid, "section": "bullet", "text": text, "fields": {}, "parent": "exp1",
            "source_refs": list(refs), "origin": "ai", "covers": [], "violations": violations or []}


def test_create_is_idempotent(env):
    new_user, add_job, _, client_as = env
    uid = new_user()
    jid = add_job(uid)
    c = client_as(uid)
    first = c.post("/applications", json={"job_id": jid})
    second = c.post("/applications", json={"job_id": jid})
    assert first.status_code in (200, 201) and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["state"] == "draft"


def test_create_is_idempotent_under_concurrency(env):
    new_user, add_job, _, client_as = env
    uid = new_user()
    jid = add_job(uid)
    ids, lock = [], threading.Lock()

    def post():
        r = TestClient(app).post("/applications", json={"job_id": jid})
        with lock:
            ids.append(r.json()["id"])

    app.dependency_overrides[current_user_id] = lambda: uid
    threads = [threading.Thread(target=post) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 1
    with SessionLocal() as s:
        assert len(s.execute(select(Application).where(Application.user_id == uid)).scalars().all()) == 1


def test_create_job_of_other_user_404(env):
    new_user, add_job, _, client_as = env
    owner, other = new_user(), new_user()
    jid = add_job(owner)
    r = client_as(other).post("/applications", json={"job_id": jid})
    assert r.status_code == 404 and r.json()["detail"] == "Vaga não encontrada."


def test_initial_locale_and_template(env):
    new_user, add_job, _, client_as = env
    uid = new_user()
    c = client_as(uid)
    a = c.post("/applications", json={"job_id": add_job(uid, language="unknown", country="AT")}).json()
    assert a["locale"] == "en" and a["template"] == "de"
    b = c.post("/applications", json={"job_id": add_job(uid, language="pt", country=None)}).json()
    assert b["locale"] == "pt" and b["template"] == "us"


def test_list_and_detail_scoped_by_user(env):
    new_user, add_job, add_application, client_as = env
    mine, theirs = new_user(), new_user()
    aid = add_application(mine, add_job(mine), lines=[_fact()])
    other_id = add_application(theirs, add_job(theirs), lines=[_fact()])
    c = client_as(mine)
    ids = [i["id"] for i in c.get("/applications").json()["items"]]
    assert ids == [aid]
    assert c.get(f"/applications/{other_id}").status_code == 404


def test_detail_has_no_job_description(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact()])
    body = client_as(uid).get(f"/applications/{aid}").json()
    assert "Kubernetes" not in str(body) and "description" not in body["job"]


def test_generate_409_when_generating_and_when_applied(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    gen = add_application(uid, add_job(uid), state="generating")
    applied = add_application(uid, add_job(uid), state="applied", lines=[_fact()])
    c = client_as(uid)
    r1 = c.post(f"/applications/{gen}/generate")
    assert r1.status_code == 409 and r1.json()["detail"] == api_apps.MSG_GENERATING
    r2 = c.post(f"/applications/{applied}/generate")
    assert r2.status_code == 409 and r2.json()["detail"] == api_apps.MSG_APPLIED_LOCKED


def test_generate_409_without_vectorized_profile(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user(vectorized=False)
    aid = add_application(uid, add_job(uid), state="draft")
    r = client_as(uid).post(f"/applications/{aid}/generate")
    assert r.status_code == 409 and r.json()["detail"] == api_apps.MSG_NOT_VECTORIZED


def test_generate_starts_and_returns_generating(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft")
    r = client_as(uid).post(f"/applications/{aid}/generate")
    assert r.status_code == 202 and r.json()["state"] == "generating"


def test_generate_429_at_daily_limit(env, monkeypatch):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft")
    monkeypatch.setattr(api_apps, "generated_last_24h", lambda session: get_settings().application_daily_limit)
    r = client_as(uid).post(f"/applications/{aid}/generate")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "3600"
    assert r.json()["detail"] == api_apps.MSG_DAILY_LIMIT.format(n=get_settings().application_daily_limit)


def test_detail_warns_profile_changed_job_changed_job_closed(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    jid = add_job(uid)
    aid = add_application(uid, jid, lines=[_fact()], profile_version=1, job_hash="old")
    with SessionLocal() as s:
        s.add(Profile(user_id=uid, version=2, data=_profile_data(), content_hash="p2"))
        s.execute(Job.__table__.update().where(Job.id == jid).values(status="closed"))
        s.commit()
    warnings = client_as(uid).get(f"/applications/{aid}").json()["warnings"]
    assert set(warnings) == {"profile_changed", "job_changed", "job_closed"}


def test_edit_ai_line_valid_keeps_valid(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact(), _ai()])
    r = client_as(uid).put(f"/applications/{aid}/lines/L2", json={"text": "Cortei o tempo do pipeline com Airflow"})
    assert r.status_code == 200
    line = next(x for x in r.json()["lines"] if x["id"] == "L2")
    assert line["violations"] == [] and line["origin"] == "user"
    assert r.json()["status"] == "valid"


def test_edit_line_with_invented_number_needs_fix(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact(), _ai()])
    r = client_as(uid).put(f"/applications/{aid}/lines/L2", json={"text": "Reduzi o tempo em 60% com Airflow"})
    line = next(x for x in r.json()["lines"] if x["id"] == "L2")
    assert line["violations"] and line["violations"][0].startswith("Número")
    assert r.json()["status"] == "needs_fix"


def test_fixing_line_restores_valid_and_coverage(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact(), _ai(text="Reduzi em 60% com Airflow")])
    c = client_as(uid)
    r = c.put(f"/applications/{aid}/lines/L2", json={"text": "Reduzi o pipeline com Airflow"})
    assert r.json()["status"] == "valid"


def test_edit_fact_line_409(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact(), _ai()])
    r = client_as(uid).put(f"/applications/{aid}/lines/L1", json={"text": "Outra empresa"})
    assert r.status_code == 409 and r.json()["detail"] == api_apps.MSG_FACT_LINE


def test_patch_template_and_referees_rules(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact()])
    c = client_as(uid)
    assert c.patch(f"/applications/{aid}", json={"referees": "Dr. X"}).status_code == 422
    assert c.patch(f"/applications/{aid}", json={"template": "au", "referees": "Dr. X"}).json()["referees"] == "Dr. X"
    assert c.patch(f"/applications/{aid}", json={"template": "fr"}).status_code == 422
    assert c.patch(f"/applications/{aid}", json={"template": "de"}).json()["template"] == "de"


def test_pdf_and_txt_blocked_while_needs_fix(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact(), _ai(violations=["Número, data ou valor que não está no item citado: 60%"])])
    c = client_as(uid)
    for path in ("resume.pdf", "resume.txt"):
        r = c.get(f"/applications/{aid}/{path}")
        assert r.status_code == 409 and r.json()["detail"] == api_apps.MSG_PDF_BLOCKED


def test_pdf_503_when_unavailable_txt_still_works(env, monkeypatch):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), lines=[_fact(), _ai()])

    def unavailable(html):
        raise api_apps.PdfUnavailable("libpango missing")

    monkeypatch.setattr(api_apps, "render_pdf", unavailable)
    c = client_as(uid)
    r = c.get(f"/applications/{aid}/resume.pdf")
    assert r.status_code == 503 and r.json()["detail"] == api_apps.MSG_PDF_UNAVAILABLE
    t = c.get(f"/applications/{aid}/resume.txt")
    assert t.status_code == 200 and "Reduzi o tempo" in t.text


def test_pdf_served_as_attachment_named_by_id(env, monkeypatch):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid, title="Vaga <b>secreta</b>"), lines=[_fact(), _ai()])
    monkeypatch.setattr(api_apps, "render_pdf", lambda html: b"%PDF-1.4 fake")
    r = client_as(uid).get(f"/applications/{aid}/resume.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert r.headers["content-disposition"] == f'attachment; filename="curriculo-{aid}.pdf"'
    assert "secreta" not in r.headers["content-disposition"]


def test_download_other_user_404(env):
    new_user, add_job, add_application, client_as = env
    owner, other = new_user(), new_user()
    aid = add_application(owner, add_job(owner), lines=[_fact(), _ai()])
    assert client_as(other).get(f"/applications/{aid}/resume.txt").status_code == 404


# ---- gaps -----------------------------------------------------------------

def _with_requirement(aid, *, covered=False):
    """Give the application one must requirement ('Terraform') and its coverage."""
    with SessionLocal() as s:
        a = s.get(Application, aid)
        a.requirements = {"must": [{"id": "m1", "text": "Terraform"}], "nice": [], "keywords": []}
        a.coverage = {
            "must": {"covered": int(covered), "total": 1}, "nice": {"covered": 0, "total": 0},
            "items": [{"id": "m1", "kind": "must", "text": "Terraform", "covered": covered,
                       "line_ids": [], "decision": None}],
        }
        s.commit()


def _profile_versions(uid):
    with SessionLocal() as s:
        return sorted(s.execute(select(Profile.version).where(Profile.user_id == uid)).scalars().all())


def test_have_creates_new_profile_version_and_regenerates(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="ready", lines=[_fact(), _ai()])
    _with_requirement(aid)
    r = client_as(uid).post(f"/applications/{aid}/gaps/m1/resolve", json={"action": "have", "text": "Terraform"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile_version"] == 2 and body["regenerated"] is True
    assert _profile_versions(uid) == [1, 2]
    with SessionLocal() as s:
        assert s.get(Application, aid).state == "generating"


def test_have_reembeds_only_changed_chunk(env):
    new_user, add_job, add_application, client_as = env
    from rumo_core.profile.schema import CanonicalProfile
    from rumo_core.profile.service import confirm_profile

    uid = new_user(profile=False)
    with SessionLocal() as s:
        confirm_profile(s, uid, CanonicalProfile.model_validate(_profile_data()), None)
        s.execute(ProfileChunk.__table__.update().where(ProfileChunk.user_id == uid).values(
            embedding=_vec(), model_version=MODEL_VERSION))
        s.commit()
    aid = add_application(uid, add_job(uid), state="ready", lines=[_fact(), _ai()])
    _with_requirement(aid)
    r = client_as(uid).post(f"/applications/{aid}/gaps/m1/resolve", json={"action": "have", "text": "Terraform"})
    assert r.status_code == 200 and r.json()["profile_version"] == 2
    with SessionLocal() as s:
        rows = {c.source_ref: c.embedding for c in s.execute(
            select(ProfileChunk).where(ProfileChunk.user_id == uid, ProfileChunk.profile_version == 2)).scalars()}
    assert rows["summary"] is not None and rows["exp1"] is not None  # unchanged: reused
    assert rows["skills"] is None  # the only changed chunk is embedded again


def test_lack_does_not_change_profile(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="ready", lines=[_fact(), _ai()])
    _with_requirement(aid)
    r = client_as(uid).post(f"/applications/{aid}/gaps/m1/resolve", json={"action": "lack"})
    assert r.status_code == 200
    assert r.json()["profile_version"] is None and r.json()["regenerated"] is False
    assert r.json()["coverage"]["items"][0]["decision"] == "lack"
    assert _profile_versions(uid) == [1]


def test_have_at_daily_limit_updates_profile_without_regenerating(env, monkeypatch):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="ready", lines=[_fact(), _ai()])
    _with_requirement(aid)
    monkeypatch.setattr(api_apps, "generated_last_24h", lambda session: get_settings().application_daily_limit)
    r = client_as(uid).post(f"/applications/{aid}/gaps/m1/resolve", json={"action": "have", "text": "Terraform"})
    assert r.status_code == 200 and r.json()["regenerated"] is False
    assert _profile_versions(uid) == [1, 2]
    with SessionLocal() as s:
        assert s.get(Application, aid).state == "ready"


def test_resolve_covered_requirement_409(env):
    new_user, add_job, add_application, client_as = env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="ready", lines=[_fact(), _ai()])
    _with_requirement(aid, covered=True)
    r = client_as(uid).post(f"/applications/{aid}/gaps/m1/resolve", json={"action": "lack"})
    assert r.status_code == 409 and r.json()["detail"] == api_apps.MSG_ALREADY_COVERED


def test_resolve_unknown_requirement_and_other_user_404(env):
    new_user, add_job, add_application, client_as = env
    owner, other = new_user(), new_user()
    aid = add_application(owner, add_job(owner), state="ready", lines=[_fact(), _ai()])
    _with_requirement(aid)
    assert client_as(owner).post(f"/applications/{aid}/gaps/zz/resolve", json={"action": "lack"}).status_code == 404
    assert client_as(other).post(f"/applications/{aid}/gaps/m1/resolve", json={"action": "lack"}).status_code == 404


# ---- questions ------------------------------------------------------------

class FakeGreenhouse:
    def __init__(self, raw=None, exc=None):
        self.raw = raw or []
        self.exc = exc
        self.calls = 0

    async def fetch_job_questions(self, identifier, external_id):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.raw


def _use_source(fake):
    from api.sources import source_getter

    app.dependency_overrides[source_getter] = lambda: (lambda name: fake)


@pytest.fixture
def questions_env(env):
    new_user, add_job, add_application, client_as = env
    yield new_user, add_job, add_application, client_as
    from api.sources import source_getter

    app.dependency_overrides.pop(source_getter, None)


def test_fetch_questions_from_greenhouse_once(questions_env):
    from rumo_core.sources.base import RawQuestion

    new_user, add_job, add_application, client_as = questions_env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft")
    fake = FakeGreenhouse(raw=[
        RawQuestion("Why Anthropic?", "textarea", [], True, "questions"),
        RawQuestion("Do you require visa sponsorship?", "multi_value_single_select", ["Yes", "No"], True, "questions"),
    ])
    _use_source(fake)
    c = client_as(uid)
    first = c.post(f"/applications/{aid}/questions/fetch").json()
    second = c.post(f"/applications/{aid}/questions/fetch").json()
    assert [q["label"] for q in first] == ["Why Anthropic?", "Do you require visa sponsorship?"]
    assert first == second and fake.calls == 1
    assert first[1]["sensitive"] is True and first[0]["sensitive"] is False


def test_fetch_non_greenhouse_409(questions_env):
    new_user, add_job, add_application, client_as = questions_env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft")
    with SessionLocal() as s:
        s.execute(Company.__table__.update().where(Company.user_id == uid).values(source="lever"))
        s.commit()
    _use_source(FakeGreenhouse())
    r = client_as(uid).post(f"/applications/{aid}/questions/fetch")
    assert r.status_code == 409 and r.json()["detail"] == api_apps.MSG_NOT_GREENHOUSE


def test_fetch_failure_503_falls_back_to_paste(questions_env):
    from rumo_core.sources.base import SourceUnavailable

    new_user, add_job, add_application, client_as = questions_env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft")
    _use_source(FakeGreenhouse(exc=SourceUnavailable("down")))
    c = client_as(uid)
    r = c.post(f"/applications/{aid}/questions/fetch")
    assert r.status_code == 503 and r.json()["detail"] == api_apps.MSG_QUESTIONS_UNAVAILABLE
    pasted = c.post(f"/applications/{aid}/questions", json={"text": "Por que a empresa?\n\nDescreva um projeto"})
    assert pasted.status_code == 200 and [q["label"] for q in pasted.json()] == ["Por que a empresa?", "Descreva um projeto"]


def test_paste_appends_positions_and_flags_sensitive(questions_env):
    new_user, add_job, add_application, client_as = questions_env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft")
    c = client_as(uid)
    c.post(f"/applications/{aid}/questions", json={"text": "Primeira?"})
    out = c.post(f"/applications/{aid}/questions", json={"text": "Qual é a sua pretensão salarial?"}).json()
    assert [q["position"] for q in out] == [1, 2]
    assert out[1]["sensitive"] is True and out[1]["source"] == "pasted"


def test_save_to_bank_uses_application_locale(questions_env):
    new_user, add_job, add_application, client_as = questions_env
    uid = new_user()
    aid = add_application(uid, add_job(uid), state="draft", locale="de")
    c = client_as(uid)
    qid = c.post(f"/applications/{aid}/questions", json={"text": "Por que nós?"}).json()[0]["id"]
    r = c.put(f"/applications/{aid}/questions/{qid}", json={"answer": "Weil ich das Team mag.", "final": True,
                                                           "save_to_bank": "why_us"})
    assert r.status_code == 200 and r.json()["answer_state"] == "final" and r.json()["bank_key"] == "why_us"
    with SessionLocal() as s:
        item = s.execute(select(AnswerBankItem).where(AnswerBankItem.user_id == uid, AnswerBankItem.key == "why_us")).scalar_one()
        assert item.lang == "de" and item.value == "Weil ich das Team mag."


def test_bank_suggestion_only_for_sensitive_and_only_own_bank(questions_env):
    new_user, add_job, add_application, client_as = questions_env
    owner, other = new_user(), new_user()
    with SessionLocal() as s:
        s.add(AnswerBankItem(user_id=owner, key="salary_expectation", value="R$ 8.000"))
        s.add(AnswerBankItem(user_id=other, key="salary_expectation", value="US$ 9.000"))
        s.commit()
    aid = add_application(owner, add_job(owner), state="draft")
    c = client_as(owner)
    c.post(f"/applications/{aid}/questions", json={"text": "Qual é a sua pretensão salarial?"})
    c.post(f"/applications/{aid}/questions", json={"text": "Por que nós?"})
    qs = c.get(f"/applications/{aid}").json()["questions"]
    salary, why = qs[0], qs[1]
    assert salary["bank_suggestion"] == {"key": "salary_expectation", "value": "R$ 8.000"}
    assert why["bank_suggestion"] is None
