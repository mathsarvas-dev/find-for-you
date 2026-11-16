import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import api.sources as api_sources
from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, User
from rumo_core.sources.http import SafeClient

pytestmark = pytest.mark.db

PUBLIC = "93.184.216.34"


def _posting() -> dict:
    return {
        "@type": "JobPosting", "title": "Analista de dados", "identifier": "42",
        "description": "<p>Python e SQL.</p>", "datePosted": "2026-11-10",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Ltda"},
    }


def _page(data: dict | None) -> str:
    block = ""
    if data is not None:
        block = f'<script type="application/ld+json">{json.dumps(data)}</script>'
    return f"<html><head>{block}</head><body>vaga</body></html>"


class Pages:
    """Serves pages by host+path and records every request path."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[str] = []

    def client(self) -> SafeClient:
        def handler(request):
            key = request.headers.get("host", "") + request.url.path
            self.calls.append(key)
            if key.endswith("/robots.txt"):
                return httpx.Response(404)
            status, body = self.pages.get(key, (404, ""))
            return httpx.Response(status, headers={"content-type": "text/html"},
                                  content=body.encode())

        async def resolve(host, port):
            return [PUBLIC]

        async def no_sleep(seconds):
            return None

        return SafeClient(httpx.MockTransport(handler), resolve=resolve, sleep=no_sleep,
                          clock=lambda: 0.0)


@pytest.fixture
def users():
    with SessionLocal() as s:
        tag = uuid.uuid4().hex
        rows = [User(email=f"api-import-{tag}-{i}@example.test") for i in (1, 2)]
        s.add_all(rows)
        s.commit()
        ids = [u.id for u in rows]
    yield ids
    with SessionLocal() as s:
        for uid in ids:
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


@pytest.fixture
def setup(users):
    api_sources._IMPORTS.clear()
    pages = Pages({
        "jobs.acme.test/vagas/42": (200, _page(_posting())),
        "jobs.acme.test/vagas/43": (200, _page(None)),
        "www.linkedin.com/jobs/view/7": (200, _page(_posting())),
    })
    app.dependency_overrides[current_user_id] = lambda: users[0]
    app.dependency_overrides[api_sources.safe_client] = pages.client
    yield TestClient(app), pages, users
    app.dependency_overrides.clear()
    api_sources._IMPORTS.clear()


def _post(client, **body):
    return client.post("/jobs/import-link", json=body)


def test_import_link_201_then_200(setup):
    client, _pages, _users = setup
    url = "https://jobs.acme.test/vagas/42"
    first = _post(client, url=url)
    assert first.status_code == 201
    assert first.json()["created"] is True
    second = _post(client, url=url)
    assert second.status_code == 200
    assert second.json() == {"job_id": first.json()["job_id"], "created": False}


def test_import_link_needs_text_422_code(setup):
    client, _pages, _users = setup
    resp = _post(client, url="https://jobs.acme.test/vagas/43")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "needs_text"
    assert detail["reason"] == "no_structured_data"
    assert detail["message"] == api_sources.MSG_NEEDS_TEXT


def test_import_link_with_pasted_text_skips_fetch(setup):
    client, pages, _users = setup
    resp = _post(client, url="https://jobs.acme.test/vagas/43", title="Vaga",
                 description="Texto colado.")
    assert resp.status_code == 201
    assert pages.calls == []


def test_import_link_title_without_description_422(setup):
    client, _pages, _users = setup
    resp = _post(client, url="https://jobs.acme.test/vagas/43", title="Só título")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Informe o título e a descrição juntos."


def test_import_link_unsafe_url_422(setup):
    client, _pages, _users = setup
    resp = _post(client, url="http://10.0.0.1/vagas/1")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Endereço não permitido."


def test_import_link_user_scoped(setup):
    client, _pages, users = setup
    url = "https://jobs.acme.test/vagas/42"
    first = _post(client, url=url)
    app.dependency_overrides[current_user_id] = lambda: users[1]
    second = _post(client, url=url)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["job_id"] != second.json()["job_id"]


def test_import_link_works_for_blocked_portal(setup):
    client, _pages, _users = setup
    resp = _post(client, url="https://www.linkedin.com/jobs/view/7")
    assert resp.status_code == 201


def test_import_link_rate_limited(setup):
    client, _pages, _users = setup
    for n in range(api_sources.IMPORT_LIMIT):
        resp = _post(client, url=f"https://jobs.acme.test/colado/{n}", title="Vaga",
                     description="texto")
        assert resp.status_code == 201
    resp = _post(client, url="https://jobs.acme.test/colado/extra", title="Vaga",
                 description="texto")
    assert resp.status_code == 429
    assert resp.json()["detail"] == "Muitas importações em pouco tempo. Tente de novo mais tarde."


def test_import_link_rejects_unknown_fields(setup):
    client, _pages, _users = setup
    resp = client.post("/jobs/import-link", json={"url": "https://jobs.acme.test/x", "x": 1})
    assert resp.status_code == 422
