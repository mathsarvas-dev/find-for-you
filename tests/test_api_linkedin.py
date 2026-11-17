import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

import api.linkedin as api_linkedin
from api.chat_filters import HourlyLimiter
from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.llm.base import LLMError, LLMRateLimited
from rumo_core.models import LinkedinOptimization, Profile, User
from rumo_core.profile.schema import Basics, CanonicalProfile, Experience, Skill

pytestmark = pytest.mark.db


class FakeLLM:
    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = 0

    async def complete_json(self, messages, schema):
        self.calls += 1
        if self.error:
            raise self.error
        return schema.model_validate(self.responses.pop(0))


GOOD = {
    "headline": {"text": "Engenheira de dados com Airflow", "source_refs": ["exp1", "sk1"]},
    "about": [{"text": "Engenheira de dados com foco em Airflow.", "source_refs": ["exp1"]}],
    "viewpoints": [{"viewpoint": "recruiter", "notes": [{"text": "Mostre a área de atuação."}]}],
    "improvements": [{"priority": "high", "section": "about", "text": "Escreva um parágrafo de abertura."}],
}


@pytest.fixture
def env():
    created: list[int] = []
    state = {"llm": FakeLLM(GOOD), "limiter": HourlyLimiter(max_calls=5)}

    def new_user(*, provisional=False, with_profile=True):
        with SessionLocal() as s:
            u = User(email=f"api-li-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            if with_profile:
                s.add(Profile(
                    user_id=u.id, version=1, provisional=provisional, content_hash="p",
                    data=CanonicalProfile(
                        basics=Basics(name="Ana"),
                        experiences=[Experience(id="exp1", company="Acme", title="Engenheira de Dados")],
                        skills=[Skill(id="sk1", name="Airflow")],
                    ).model_dump(mode="json"),
                ))
                s.commit()
            return u.id

    def client(uid):
        app.dependency_overrides[current_user_id] = lambda: uid
        app.dependency_overrides[api_linkedin.get_linkedin_llm] = lambda: state["llm"]
        app.dependency_overrides[api_linkedin.get_linkedin_limiter] = lambda: state["limiter"]
        return TestClient(app)

    yield new_user, client, state
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(LinkedinOptimization).where(LinkedinOptimization.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


def test_get_is_empty_before_the_first_run(env):
    new_user, client, _ = env
    r = client(new_user()).get("/linkedin/optimization")
    assert r.status_code == 200 and r.json() == {"optimization": None}


def test_generate_saves_and_get_returns_it(env):
    new_user, client, state = env
    uid = new_user()
    c = client(uid)
    r = c.post("/linkedin/optimization", json={"language": "pt"})
    assert r.status_code == 200, r.text
    opt = r.json()["optimization"]
    assert opt["stale"] is False and opt["profile_version"] == 1
    assert opt["result"]["headline"]["text"] == "Engenheira de dados com Airflow"
    assert [v["key"] for v in opt["result"]["viewpoints"]] == [
        "recruiter", "ats", "technical_manager", "international"]
    assert c.get("/linkedin/optimization").json()["optimization"]["result"] == opt["result"]

    state["llm"] = FakeLLM(GOOD)
    assert c.post("/linkedin/optimization", json={}).status_code == 200
    with SessionLocal() as s:  # replaced, not accumulated
        assert len(s.scalars(select(LinkedinOptimization).where(LinkedinOptimization.user_id == uid)).all()) == 1


def test_stale_after_the_profile_changes(env):
    new_user, client, _ = env
    uid = new_user()
    c = client(uid)
    c.post("/linkedin/optimization", json={})
    with SessionLocal() as s:
        s.add(Profile(user_id=uid, version=2, content_hash="q", data={}))
        s.commit()
    assert c.get("/linkedin/optimization").json()["optimization"]["stale"] is True


def test_refuses_without_or_with_provisional_profile(env):
    new_user, client, state = env
    r = client(new_user(with_profile=False)).post("/linkedin/optimization", json={})
    assert r.status_code == 409 and "Confirme seu perfil" in r.json()["detail"]
    r = client(new_user(provisional=True)).post("/linkedin/optimization", json={})
    assert r.status_code == 409 and "texto colado" in r.json()["detail"]
    assert state["llm"].calls == 0


def test_no_llm_configured_is_503(env):
    new_user, client, state = env
    state["llm"] = None
    r = client(new_user()).post("/linkedin/optimization", json={})
    assert r.status_code == 503 and "chave" in r.json()["detail"]


@pytest.mark.parametrize("error", [LLMRateLimited("x"), LLMError("x")])
def test_llm_failures_are_503_and_nothing_is_saved(env, error):
    new_user, client, state = env
    state["llm"] = FakeLLM(error=error)
    c = client(new_user())
    r = c.post("/linkedin/optimization", json={})
    assert r.status_code == 503 and "ocupado" in r.json()["detail"]
    assert c.get("/linkedin/optimization").json() == {"optimization": None}


def test_nothing_usable_is_422(env):
    new_user, client, state = env
    state["llm"] = FakeLLM({})
    r = client(new_user()).post("/linkedin/optimization", json={})
    assert r.status_code == 422 and "sem inventar" in r.json()["detail"]


def test_hourly_limit_is_429_with_retry_after(env):
    new_user, client, state = env
    state["limiter"] = HourlyLimiter(max_calls=1)
    state["llm"] = FakeLLM(GOOD, GOOD)
    c = client(new_user())
    assert c.post("/linkedin/optimization", json={}).status_code == 200
    r = c.post("/linkedin/optimization", json={})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_rejects_unknown_language_and_extra_fields(env):
    new_user, client, _ = env
    c = client(new_user())
    assert c.post("/linkedin/optimization", json={"language": "fr"}).status_code == 422
    assert c.post("/linkedin/optimization", json={"x": 1}).status_code == 422


def test_results_are_per_user(env):
    new_user, client, _ = env
    a, b = new_user(), new_user()
    client(a).post("/linkedin/optimization", json={})
    assert client(b).get("/linkedin/optimization").json() == {"optimization": None}
