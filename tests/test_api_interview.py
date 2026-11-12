import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError

from api.main import app, current_user_id
from rumo_core.db import SessionLocal, get_session
from rumo_core.models import (
    AnswerBankItem,
    ChatMessage,
    InterviewSession,
    Profile,
    ProfileChunk,
    ProfileSuggestion,
    User,
)
from rumo_core.profile.schema import Achievement, CanonicalProfile, Experience, Skill
from rumo_core.profile.service import confirm_profile

ROOT = Path(__file__).resolve().parents[1]
GAP = "achievement_without_number:e1"


def _profile():
    return CanonicalProfile(
        summary="Dev",
        experiences=[Experience(
            id="e1", title="Dev", company="Acme", start="2019", description="Backend",
            achievements=[Achievement(id="a1", text="Migrei o banco")])],
        skills=[Skill(id="s1", name="Python")],
    )


@pytest.fixture
def uids():
    with SessionLocal() as s:
        tag = uuid.uuid4().hex
        users = [User(email=f"api-iv-{tag}-{i}@example.test") for i in (1, 2)]
        s.add_all(users)
        s.commit()
        ids = [u.id for u in users]
    yield ids
    with SessionLocal() as s:
        for model in (ProfileSuggestion, ChatMessage, AnswerBankItem, InterviewSession,
                      ProfileChunk, Profile):
            s.execute(delete(model).where(model.user_id.in_(ids)))
        s.execute(delete(User).where(User.id.in_(ids)))
        s.commit()


@pytest.fixture
def client(uids):
    app.dependency_overrides[current_user_id] = lambda: uids[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _confirm(uid, profile=None):
    with SessionLocal() as s:
        confirm_profile(s, uid, profile or _profile(), None)


def _set_state(sid, state, **kw):
    with SessionLocal() as s:
        row = s.get(InterviewSession, sid)
        row.state = state
        for k, v in kw.items():
            setattr(row, k, v)
        s.commit()


def _start(client, uid):
    _confirm(uid)
    r = client.post("/interview")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add_suggestion(uid, sid, payload, gap=GAP, answer="Reduzi custos em 30%"):
    with SessionLocal() as s:
        msg = ChatMessage(user_id=uid, session_id=sid, role="user", kind="answer",
                          content=answer, gap_id=gap)
        s.add(msg)
        s.flush()
        sug = ProfileSuggestion(user_id=uid, session_id=sid, message_id=msg.id, gap_id=gap,
                                op=payload["op"], payload=payload)
        s.add(sug)
        s.commit()
        return sug.id


ACH = {"op": "append_achievement", "target_ref": "e1", "text": "Reduzi custos em 30%"}


@pytest.mark.db
def test_start_without_profile_409(client):
    r = client.post("/interview")
    assert r.status_code == 409
    assert r.json() == {"detail": "Confirme seu perfil antes de iniciar a entrevista."}


@pytest.mark.db
def test_start_then_get_current_shape(client, uids):
    sid = _start(client, uids[0])
    again = client.post("/interview")
    assert again.status_code == 200 and again.json()["id"] == sid
    _set_state(sid, "awaiting_answer", asked_count=1, current_gap_id=GAP)
    with SessionLocal() as s:
        s.add(ChatMessage(user_id=uids[0], session_id=sid, role="assistant", kind="question",
                          content="Quanto?", gap_id=GAP))
        s.commit()
    sug = _add_suggestion(uids[0], sid, ACH)
    j = client.get("/interview/current").json()
    assert j["id"] == sid and j["state"] == "awaiting_answer" and j["error"] is None
    assert j["progress"] == {"asked": 1, "max": 12}
    assert [m["role"] for m in j["messages"]] == ["assistant", "user"]
    assert set(j["messages"][0]) == {"id", "role", "kind", "content", "gap_id"}
    assert j["messages"][0]["id"] < j["messages"][1]["id"]
    [s0] = j["suggestions"]
    assert s0["id"] == sug and s0["status"] == "pending" and s0["applied_version"] is None
    assert s0["payload"]["text"] == "Reduzi custos em 30%"
    assert "Migrei o banco" in s0["before"] and s0["after"] == "Reduzi custos em 30%"
    assert set(s0) == {"id", "message_id", "gap_id", "op", "payload", "status",
                       "applied_version", "before", "after"}


@pytest.mark.db
def test_get_current_prefers_active_then_latest_done(client, uids):
    sid = _start(client, uids[0])
    _set_state(sid, "done")
    assert client.get("/interview/current").json()["id"] == sid
    sid2 = client.post("/interview").json()["id"]
    assert sid2 != sid
    assert client.get("/interview/current").json()["id"] == sid2


@pytest.mark.db
def test_get_current_404_when_none(client):
    r = client.get("/interview/current")
    assert r.status_code == 404
    assert isinstance(r.json()["detail"], str)


@pytest.mark.db
def test_answer_wrong_state_409_and_empty_422_and_too_long_422(client, uids):
    sid = _start(client, uids[0])
    r = client.post(f"/interview/{sid}/answer", json={"text": "oi"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Não há pergunta aguardando resposta."}
    _set_state(sid, "awaiting_answer", current_gap_id=GAP)
    r = client.post(f"/interview/{sid}/answer", json={"text": "   "})
    assert r.status_code == 422
    assert r.json() == {"detail": "A resposta não pode ser vazia."}
    r = client.post(f"/interview/{sid}/answer", json={"text": "x" * 4001})
    assert r.status_code == 422
    assert r.json() == {"detail": "A resposta pode ter no máximo 4000 caracteres."}
    for body in ({}, {"text": 5}, {"text": None}):
        r = client.post(f"/interview/{sid}/answer", json=body)
        assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    ok = client.post(f"/interview/{sid}/answer", json={"text": "Reduzi 30%"})
    assert ok.status_code == 202 and ok.json() == {"state": "generating"}


@pytest.mark.db
def test_skip_finish_retry_transitions(client, uids):
    sid = _start(client, uids[0])
    assert client.post(f"/interview/{sid}/skip").status_code == 409
    assert client.post(f"/interview/{sid}/retry").status_code == 409
    _set_state(sid, "awaiting_answer", current_gap_id=GAP)
    assert client.post(f"/interview/{sid}/skip").json() == {"state": "generating"}
    _set_state(sid, "error", error="falhou")
    assert client.post(f"/interview/{sid}/retry").json() == {"state": "generating"}
    assert client.post(f"/interview/{sid}/finish").json() == {"state": "done"}
    r = client.post(f"/interview/{sid}/finish")
    assert r.status_code == 409 and r.json() == {"detail": "A entrevista já foi encerrada."}


@pytest.mark.db
def test_cross_user_session_and_suggestion_404(client, uids):
    sid = _start(client, uids[0])
    _set_state(sid, "awaiting_answer", current_gap_id=GAP)
    sug = _add_suggestion(uids[0], sid, ACH)
    app.dependency_overrides[current_user_id] = lambda: uids[1]
    other = TestClient(app)
    for path, body in ((f"/interview/{sid}/answer", {"text": "oi"}),
                       (f"/interview/{sid}/skip", None), (f"/interview/{sid}/finish", None),
                       (f"/interview/{sid}/retry", None),
                       (f"/interview/suggestions/{sug}/resolve", {"action": "reject"})):
        r = other.post(path, json=body) if body else other.post(path)
        assert r.status_code == 404, path
    assert other.get("/interview/current").status_code == 404
    with SessionLocal() as s:
        assert s.get(ProfileSuggestion, sug).status == "pending"
        assert s.get(InterviewSession, sid).state == "awaiting_answer"


@pytest.mark.db
def test_resolve_accept_returns_version_and_get_profile_reflects_it(client, uids):
    sid = _start(client, uids[0])
    sug = _add_suggestion(uids[0], sid, ACH)
    r = client.post(f"/interview/suggestions/{sug}/resolve", json={"action": "accept"})
    assert r.status_code == 200
    assert r.json() == {"status": "accepted", "applied_version": 2}
    p = client.get("/profile").json()
    assert p["version"] == 2
    texts = [a["text"] for a in p["profile"]["experiences"][0]["achievements"]]
    assert "Reduzi custos em 30%" in texts


@pytest.mark.db
def test_resolve_reject_and_adjust_validation(client, uids):
    sid = _start(client, uids[0])
    sug = _add_suggestion(uids[0], sid, ACH)
    r = client.post(f"/interview/suggestions/{sug}/resolve", json={"action": "adjust"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = client.post(f"/interview/suggestions/{sug}/resolve", json={"action": "bogus"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    bad = {**ACH, "text": "Aumentei vendas em 99%"}
    r = client.post(f"/interview/suggestions/{sug}/resolve",
                    json={"action": "adjust", "payload": bad})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Não foi possível aplicar a sugestão")
    r = client.post(f"/interview/suggestions/{sug}/resolve", json={"action": "reject"})
    assert r.json() == {"status": "rejected", "applied_version": None}


@pytest.mark.db
def test_resolve_double_post_second_409(client, uids):
    sid = _start(client, uids[0])
    sug = _add_suggestion(uids[0], sid, ACH)
    assert client.post(f"/interview/suggestions/{sug}/resolve",
                       json={"action": "accept"}).status_code == 200
    r = client.post(f"/interview/suggestions/{sug}/resolve", json={"action": "accept"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Esta sugestão já foi resolvida."}


@pytest.mark.db
def test_resolve_target_missing_409_message(client, uids):
    sid = _start(client, uids[0])
    gone = {"op": "append_achievement", "target_ref": "e9", "text": "Reduzi custos em 30%"}
    sug = _add_suggestion(uids[0], sid, gone, gap="achievement_without_number:e9")
    r = client.post(f"/interview/suggestions/{sug}/resolve", json={"action": "accept"})
    assert r.status_code == 409
    assert r.json() == {
        "detail": "O item do perfil a que esta sugestão se refere mudou ou não existe mais."}
    j = client.get("/interview/current").json()
    assert j["suggestions"][0]["before"] == "(item removido)"


@pytest.mark.db
def test_before_after_rendered_for_each_op(client, uids):
    sid = _start(client, uids[0])
    drafts = [
        ACH,
        {"op": "set_experience_description", "target_ref": "e1", "text": "Novo"},
        {"op": "set_experience_dates", "target_ref": "e1", "end": "2022"},
        {"op": "set_summary", "text": "Resumo novo"},
        {"op": "add_skill", "name": "Go", "level": "básico"},
        {"op": "set_preference", "key": "remote", "value": "sim"},
        {"op": "add_note", "text": "obs"},
    ]
    for d in drafts:
        _add_suggestion(uids[0], sid, d)
    got = client.get("/interview/current").json()["suggestions"]
    assert [(s["before"], s["after"]) for s in got] == [
        ("1 conquista(s); última: Migrei o banco", "Reduzi custos em 30%"),
        ("Backend", "Novo"),
        ("2019 – —", "2019 – 2022"),
        ("Dev", "Resumo novo"),
        ("Python", "Go (básico)"),
        ("—", "remote: sim"),
        ("—", "obs"),
    ]
    assert [s["id"] for s in got] == sorted(s["id"] for s in got)


@pytest.mark.db
def test_operational_error_503_shape(client):
    class Broken:
        def scalars(self, *a, **k):
            raise OperationalError("SELECT", {}, Exception("down"))
        execute = scalar = get = scalars

    app.dependency_overrides[get_session] = lambda: Broken()
    r = client.get("/interview/current")
    assert r.status_code == 503
    assert r.json() == {"detail": "Banco de dados indisponível"}


@pytest.mark.parametrize("order", [
    ["api.interview", "api.main"],
    ["api.main", "api.interview"],
    ["api.interview"],
])
def test_import_api_interview_first_in_fresh_subprocess(order):
    code = "; ".join(f"import {m}" for m in order)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=os.environ.copy(),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
