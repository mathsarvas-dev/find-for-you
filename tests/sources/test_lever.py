import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SalaryHint, SourceBadResponse, SourceNotFound
from rumo_core.sources.http import SafeClient
from rumo_core.sources.lever import LeverSource
from rumo_core.sources.normalize import normalize_job

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "lever" / "leverdemo.json"
PUBLIC = "93.184.216.34"


async def _resolve(host, port):
    return [PUBLIC]


async def _no_sleep(seconds):
    return None


def _client(handler):
    return SafeClient(httpx.MockTransport(handler), resolve=_resolve, sleep=_no_sleep)


def _json_handler(payload, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, headers={"content-type": "application/json"},
                              content=json.dumps(payload).encode())

    return handler


def _item(eid, **kw):
    base = {"id": eid, "text": f"Vaga {eid}", "hostedUrl": f"https://jobs.lever.co/acme/{eid}",
            "categories": {"location": "Remote, Brazil", "department": "Eng"},
            "createdAt": 1788199939703, "description": "<p>Texto</p>"}
    base.update(kw)
    return base


def test_parses_real_fixture():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    src = LeverSource(_client(_json_handler(data)), max_bytes=20_000_000)
    import asyncio

    jobs = asyncio.run(src.fetch_jobs("leverdemo"))
    assert [j.external_id for j in jobs] == [str(x["id"]) for x in data]
    first = jobs[0]
    assert first.url.startswith("https://jobs.lever.co/")
    assert first.department == "Customer Success"
    assert first.location_text == "Atlanta, Georgia"
    assert first.posted_at is not None and first.posted_at.tzinfo is not None
    hybrid = next(j for j in jobs if j.work_model_hint == "hybrid")
    assert hybrid.country_hint == "US"
    assert hybrid.salary_hint == SalaryHint(
        min=Decimal("85000"), max=Decimal("175000"), currency="USD", period="year"
    )


async def test_missing_fields_tolerated_and_bad_items_dropped():
    payload = [_item(1), {"id": 2}, "lixo", _item(3, text=None),
               _item(4, categories="x", workplaceType="anywhere", country="Germany")]
    jobs = await LeverSource(_client(_json_handler(payload)), max_bytes=10**6).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1", "4"]
    assert jobs[1].work_model_hint is None and jobs[1].country_hint is None
    assert jobs[1].location_text == ""


async def test_duplicate_ids_last_wins():
    payload = [_item(1, text="primeira"), _item(1, text="segunda")]
    jobs = await LeverSource(_client(_json_handler(payload)), max_bytes=10**6).fetch_jobs("acme")
    assert len(jobs) == 1 and jobs[0].title == "segunda"


async def test_hostile_html_kept_as_data():
    payload = [_item(1, description="<p>Oi</p><script>x()</script></p><img src=x onerror=y()>",
                     lists=[{"text": "<b>Req</b>", "content": "<li>Py</li>"}])]
    jobs = await LeverSource(_client(_json_handler(payload)), max_bytes=10**6).fetch_jobs("acme")
    text = normalize_job(jobs[0]).description
    assert "<script" not in text and "<img" not in text
    assert "Req" in text  # the list title stays as plain text, never as markup


async def test_not_found():
    src = LeverSource(_client(_json_handler({}, status=404)), max_bytes=10**6)
    with pytest.raises(SourceNotFound):
        await src.fetch_jobs("acme")


async def test_bad_shape():
    src = LeverSource(_client(_json_handler({"ok": True})), max_bytes=10**6)
    with pytest.raises(SourceBadResponse):
        await src.fetch_jobs("acme")
    raw = _client(lambda r: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{x"))
    with pytest.raises(SourceBadResponse):
        await LeverSource(raw, max_bytes=10**6).fetch_jobs("acme")


async def test_pagination_skip_limit():
    seen = []
    pages = {0: [_item(i) for i in range(100)], 100: [_item(i) for i in range(100, 200)],
             200: [_item(i) for i in range(200, 203)]}

    def handler(request):
        seen.append(request)
        skip = int(request.url.params["skip"])
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=json.dumps(pages[skip]).encode())

    jobs = await LeverSource(_client(handler), max_bytes=10**6).fetch_jobs("acme")
    assert len(jobs) == 203
    assert [int(r.url.params["skip"]) for r in seen] == [0, 100, 200]


async def test_page_cap_raises():
    full = [_item(i) for i in range(100)]
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=json.dumps(full).encode())

    with pytest.raises(SourceBadResponse):
        await LeverSource(_client(handler), max_bytes=10**6).fetch_jobs("acme")
    assert calls["n"] == 20


async def test_eu_identifier():
    seen = []
    src = LeverSource(_client(_json_handler([], seen=seen)), max_bytes=10**6)
    await src.fetch_jobs("eu:acme")
    assert seen[0].url.params["skip"] == "0"
    assert seen[0].headers["host"] == "api.eu.lever.co"


def test_normalize_identifier():
    src = LeverSource(None, max_bytes=1)
    assert src.normalize_identifier("https://jobs.lever.co/Leverdemo") == "leverdemo"
    assert src.normalize_identifier("jobs.eu.lever.co/acme/123") == "acme"
    assert src.normalize_identifier("eu:acme") == "eu:acme"
    assert src.normalize_identifier("acme") == "acme"
    with pytest.raises(ValueError):
        src.normalize_identifier("https://evil.test/acme")
    with pytest.raises(ValueError):
        src.normalize_identifier("bad id!")
