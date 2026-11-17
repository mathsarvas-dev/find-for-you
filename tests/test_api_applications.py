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
    Application, ApplicationDocument, Company, Job, Profile, ProfileChunk, User,
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
