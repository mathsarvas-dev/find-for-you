import io

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import OperationalError

from api.main import app, current_user_id
from rumo_core.db import SessionLocal, get_session
from rumo_core.models import Profile, ProfileChunk, ProfileImport, User
from tests.profile.test_text_extract import make_pdf

pytestmark = pytest.mark.db

PROFILE = {
    "summary": "Dev",
    "experiences": [{"company": "Acme", "title": "Dev"}],
    "skills": [{"name": "Python"}],
}


@pytest.fixture
def uids():
    with SessionLocal() as s:
        users = [User(email=f"api-prof-{i}@example.test") for i in (1, 2)]
        s.add_all(users)
        s.commit()
        ids = [u.id for u in users]
    yield ids
    with SessionLocal() as s:
        s.execute(delete(ProfileChunk).where(ProfileChunk.user_id.in_(ids)))
        s.execute(delete(Profile).where(Profile.user_id.in_(ids)))
        s.execute(delete(ProfileImport).where(ProfileImport.user_id.in_(ids)))
        s.execute(delete(User).where(User.id.in_(ids)))
        s.commit()


@pytest.fixture
def client(uids):
    app.dependency_overrides[current_user_id] = lambda: uids[0]
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upload(client, data, name="cv.pdf", ctype="application/pdf"):
    return client.post("/profile/imports", files={"file": (name, data, ctype)})


def _count_imports(uid):
    with SessionLocal() as s:
        return s.scalar(
            select(func.count()).select_from(ProfileImport).where(ProfileImport.user_id == uid)
        )


def test_upload_pdf_creates_pending_import(client, uids):
    r = _upload(client, make_pdf("Ana Silva Python"))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    with SessionLocal() as s:
        row = s.get(ProfileImport, body["id"])
        assert row.user_id == uids[0]
        assert "Ana Silva" in row.raw_text
        assert row.status == "pending"
    g = client.get(f"/profile/imports/{body['id']}")
    assert g.status_code == 200
    assert g.json() == {
        "id": body["id"], "status": "pending", "error": None,
        "truncated": False, "profile": None, "retry_at": None,
    }


def test_get_import_ready_returns_profile(client, uids):
    iid = _upload(client, make_docx_bytes()).json()["id"]
    with SessionLocal() as s:
        s.execute(update(ProfileImport).where(ProfileImport.id == iid).values(
            status="ready", extracted={"summary": "Oi"}))
        s.commit()
    j = client.get(f"/profile/imports/{iid}").json()
    assert j["status"] == "ready" and j["profile"]["summary"] == "Oi"


def make_docx_bytes():
    d = Document()
    d.add_paragraph("Ana Silva")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_upload_too_large_413(client, uids, monkeypatch):
    import api.profile as ap

    class S:
        upload_max_bytes = 100
    monkeypatch.setattr(ap, "get_settings", lambda: S())
    r = _upload(client, make_pdf("x" * 50))
    assert r.status_code == 413
    assert _count_imports(uids[0]) == 0


def test_upload_wrong_type_415(client, uids):
    r = _upload(client, b"just text", "a.txt", "text/plain")
    assert r.status_code == 415
    assert _count_imports(uids[0]) == 0


def test_upload_scanned_pdf_422_and_nothing_stored(client, uids):
    r = _upload(client, make_pdf(None))
    assert r.status_code == 422
    assert r.json()["detail"]
    assert _count_imports(uids[0]) == 0


def test_upload_corrupt_pdf_422(client, uids):
    r = _upload(client, b"%PDF-1.4 garbage")
    assert r.status_code == 422
    assert _count_imports(uids[0]) == 0


def test_get_import_other_user_404(client, uids):
    iid = _upload(client, make_pdf("Ana Silva")).json()["id"]
    app.dependency_overrides[current_user_id] = lambda: uids[1]
    assert client.get(f"/profile/imports/{iid}").status_code == 404
    assert client.get("/profile/imports/99999999").status_code == 404


def test_confirm_idempotent_double_post(client, uids):
    body = {"profile": PROFILE, "narrative": "n"}
    a = client.post("/profile/confirm", json=body)
    b = client.post("/profile/confirm", json=body)
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["version"] == b.json()["version"] == 1
    assert a.json()["chunks_total"] > 0 and a.json()["chunks_embedded"] == 0
    with SessionLocal() as s:
        n = s.scalar(select(func.count()).select_from(Profile).where(Profile.user_id == uids[0]))
    assert n == 1


def test_get_profile_404_when_empty(client):
    assert client.get("/profile").status_code == 404


def test_get_profile_reports_embedding_progress(client, uids):
    client.post("/profile/confirm", json={"profile": PROFILE, "narrative": None})
    with SessionLocal() as s:
        ch = s.scalars(select(ProfileChunk).where(ProfileChunk.user_id == uids[0])).first()
        ch.embedding = [0.1] * 1024
        ch.model_version = "m"
        s.commit()
    j = client.get("/profile").json()
    assert j["version"] == 1
    assert j["chunks_embedded"] == 1 and j["chunks_total"] >= 2
    assert j["profile"]["experiences"][0]["id"]  # stored, with filled ids


def test_operational_error_on_other_route_not_health_body(client):
    class Broken:
        def scalars(self, *a, **k):
            raise OperationalError("SELECT", {}, Exception("down"))
        execute = scalar = scalars

    app.dependency_overrides[get_session] = lambda: Broken()
    r = client.get("/profile")
    assert r.status_code == 503
    assert r.json() == {"detail": "Banco de dados indisponível"}


def _import_with_text(uid, text):
    with SessionLocal() as s:
        imp = ProfileImport(
            user_id=uid, filename="cv.txt", content_type="text/plain",
            raw_text=text, status="pending",
        )
        s.add(imp)
        s.commit()
        return imp.id


def test_import_exposes_retry_at(client, uids):
    from datetime import datetime, timezone

    iid = _import_with_text(uids[0], "Ana")
    with SessionLocal() as s:
        s.execute(update(ProfileImport).where(ProfileImport.id == iid).values(
            retry_at=datetime(2030, 1, 1, tzinfo=timezone.utc)))
        s.commit()
    body = client.get(f"/profile/imports/{iid}").json()
    assert body["status"] == "pending" and body["retry_at"].startswith("2030-01-01")


def test_continue_without_extraction_creates_provisional(client, uids):
    iid = _import_with_text(uids[0], "Ana Silva Dev Python"
        " Acme\n\nExperiencia\n\nAcme 2020")
    r = client.post(f"/profile/imports/{iid}/provisional")
    assert r.status_code == 201
    assert r.json()["chunks_total"] >= 1 and r.json()["chunks_embedded"] == 0
    g = client.get("/profile").json()
    assert g["provisional"] is True
    assert g["import"]["id"] == iid and g["import"]["status"] == "pending"
    with SessionLocal() as s:
        kinds = set(s.scalars(select(ProfileChunk.kind).where(ProfileChunk.user_id == uids[0])))
    assert kinds == {"raw"}
    ov = client.get("/overview").json()["profile"]
    assert ov["confirmed"] is True and ov["provisional"] is True
    # idempotent
    assert client.post(f"/profile/imports/{iid}/provisional").json()["version"] == 1


def test_confirm_replaces_provisional(client, uids):
    iid = _import_with_text(uids[0], "Ana Silva Dev Python")
    client.post(f"/profile/imports/{iid}/provisional")
    r = client.post("/profile/confirm", json={"profile": PROFILE})
    assert r.status_code == 200 and r.json()["version"] == 2
    g = client.get("/profile").json()
    assert g["provisional"] is False and g["import"] is None
    # a structured profile blocks a new provisional one
    assert client.post(f"/profile/imports/{iid}/provisional").status_code == 409


def test_provisional_is_user_scoped(client, uids):
    iid = _import_with_text(uids[1], "Outro usuario")
    assert client.post(f"/profile/imports/{iid}/provisional").status_code == 404
