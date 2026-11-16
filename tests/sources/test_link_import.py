import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.models import Company, Job, User
from rumo_core.sources.http import SafeClient, UnsafeURL
from rumo_core.sources.link_import import (
    LINK_COMPANY_FALLBACK,
    NeedsPastedText,
    pasted_raw,
    read_link,
    save_imported_job,
)
from rumo_core.sources.normalize import normalize_job

pytestmark = pytest.mark.db  # the save_* tests write to the shared dev DB

PUBLIC = "93.184.216.34"
URL = "https://jobs.acme.test/vagas/42"
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _posting(**kw) -> dict:
    data = {
        "@type": "JobPosting", "title": "Analista de dados", "identifier": "42",
        "description": "<p>Python e SQL.</p>", "datePosted": "2026-11-10",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Ltda"},
    }
    data.update(kw)
    return data


def _page(data: dict | None) -> str:
    block = ""
    if data is not None:
        block = f'<script type="application/ld+json">{json.dumps(data)}</script>'
    return f"<html><head>{block}</head><body>vaga</body></html>"


def _client(pages: dict, robots: str | None = None, calls: list | None = None):
    def handler(request):
        key = request.headers.get("host", "") + request.url.path
        if calls is not None:
            calls.append(key)
        if key.endswith("/robots.txt"):
            if robots is None:
                return httpx.Response(404)
            return httpx.Response(200, headers={"content-type": "text/plain"},
                                  content=robots.encode())
        status, body = pages.get(key, (404, ""))
        return httpx.Response(status, headers={"content-type": "text/html"}, content=body.encode())

    async def resolve(host, port):
        return ["10.0.0.1"] if host == "private.test" else [PUBLIC]

    async def no_sleep(seconds):
        return None

    return SafeClient(httpx.MockTransport(handler), resolve=resolve, sleep=no_sleep,
                      clock=lambda: 0.0)


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"link-import-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    yield user_id
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


async def test_read_link_jsonld():
    client = _client({"jobs.acme.test/vagas/42": (200, _page(_posting()))})
    raw = await read_link(client, URL)
    assert raw.title == "Analista de dados"
    assert len(raw.external_id) == 64  # hash of the canonical URL, like pasted text


async def test_read_link_without_jsonld_needs_text():
    client = _client({"jobs.acme.test/vagas/42": (200, _page(None))})
    with pytest.raises(NeedsPastedText) as info:
        await read_link(client, URL)
    assert info.value.reason == "no_structured_data"


async def test_read_link_robots_needs_text():
    client = _client({"jobs.acme.test/vagas/42": (200, _page(_posting()))},
                     robots="User-agent: *\nDisallow: /\n")
    with pytest.raises(NeedsPastedText) as info:
        await read_link(client, URL)
    assert info.value.reason == "robots"


async def test_read_link_private_ip_unsafe():
    with pytest.raises(UnsafeURL):
        await read_link(_client({}), "http://10.0.0.1/vagas/1")


async def test_pasted_text_is_escaped():
    raw = pasted_raw(URL, "Vaga", "<script>x()</script>\nlinha dois")
    text = normalize_job(raw).description
    # the person's markup is kept as literal text, never interpreted as an element
    assert "<script>x()</script>" in text
    assert "linha dois" in text
    assert "<p>" not in text


def test_pasted_external_id_is_hash_of_canonical_url():
    raw = pasted_raw("  HTTPS://Jobs.Acme.test/vagas/42#topo ", "Vaga", "texto")
    assert len(raw.external_id) == 64
    assert raw.external_id == pasted_raw(URL, "Outra", "x").external_id


async def test_save_imported_job_idempotent(uid):
    raw = pasted_raw(URL, "Vaga", "texto")
    with SessionLocal() as s:
        job, created = save_imported_job(s, uid, URL, raw, now=NOW)
        s.commit()
        assert created is True
    with SessionLocal() as s:
        job2, created2 = save_imported_job(s, uid, URL, raw, now=NOW)
        s.commit()
        assert created2 is False
        assert job2.id == job.id
        assert len(s.scalars(select(Job.id).where(Job.user_id == uid)).all()) == 1


async def test_imported_job_full_rank1_origin_link(uid):
    raw = pasted_raw(URL, "Vaga", "texto")
    with SessionLocal() as s:
        job, _ = save_imported_job(s, uid, URL, raw, now=NOW)
        s.commit()
        assert (job.origin, job.source_rank, job.intake, job.status) == ("link", 1, "full", "open")


async def test_link_company_from_hiring_org(uid):
    client = _client({"jobs.acme.test/vagas/42": (200, _page(_posting()))})
    raw = await read_link(client, URL)
    with SessionLocal() as s:
        job, created = save_imported_job(s, uid, URL, raw, now=NOW)
        s.commit()
        company = s.get(Company, job.company_id)
        assert created is True
        assert company.source == "link"
        assert company.origin == "link"
        assert company.active is False
        assert company.identifier == "acme"
        assert company.name == "Acme Ltda"


async def test_link_company_fallback_without_employer(uid):
    raw = pasted_raw(URL, "Vaga", "texto")
    with SessionLocal() as s:
        job, _ = save_imported_job(s, uid, URL, raw, now=NOW)
        s.commit()
        company = s.get(Company, job.company_id)
        assert company.identifier == LINK_COMPANY_FALLBACK
        assert company.name == "Vagas importadas por link"
