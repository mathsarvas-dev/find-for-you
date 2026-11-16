import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.ashby import AshbySource
from rumo_core.sources.base import SalaryHint, SourceBadResponse, SourceNotFound
from rumo_core.sources.http import SafeClient
from rumo_core.sources.normalize import normalize_job

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ashby" / "ashby.json"
PUBLIC = "93.184.216.34"


async def _resolve(host, port):
    return [PUBLIC]


async def _no_sleep(seconds):
    return None


def _src(handler):
    client = SafeClient(httpx.MockTransport(handler), resolve=_resolve, sleep=_no_sleep)
    return AshbySource(client, max_bytes=20_000_000)


def _json(payload, status=200):
    return lambda r: httpx.Response(status, headers={"content-type": "application/json"},
                                    content=json.dumps(payload).encode())


def _item(jid, **kw):
    base = {"id": jid, "title": f"Vaga {jid}", "location": "Remote - Brazil",
            "department": "Eng", "isListed": True, "isRemote": False, "workplaceType": "Remote",
            "publishedAt": "2024-03-04T14:29:08.532+00:00",
            "jobUrl": f"https://jobs.ashbyhq.com/acme/{jid}", "descriptionHtml": "<p>Texto</p>"}
    base.update(kw)
    return base


async def test_parses_real_fixture():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    jobs = await _src(_json(data)).fetch_jobs("ashby")
    assert [j.external_id for j in jobs] == [j["id"] for j in data["jobs"]]
    first = jobs[0]
    assert first.url.startswith("https://jobs.ashbyhq.com/")
    assert first.work_model_hint == "remote"
    assert first.posted_at is not None and first.posted_at.tzinfo is not None
    assert first.description_html  # descriptionHtml is kept
    salaried = [j for j in jobs if j.salary_hint is not None]
    assert salaried and salaried[0].salary_hint == SalaryHint(
        min=Decimal("150000"), max=Decimal("250000"), currency="EUR", period="year"
    )


async def test_missing_fields_tolerated():
    payload = {"jobs": [_item("1"), {"id": "2"}, "x", _item("3", title="")]}
    jobs = await _src(_json(payload)).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]


async def test_duplicate_ids_last_wins():
    payload = {"jobs": [_item("1", title="primeira"), _item("1", title="segunda")]}
    jobs = await _src(_json(payload)).fetch_jobs("acme")
    assert len(jobs) == 1 and jobs[0].title == "segunda"


async def test_hostile_html_kept_as_data():
    payload = {"jobs": [_item("1", descriptionHtml="<p>Oi</p><script>x()</script><img onerror=y()>")]}
    jobs = await _src(_json(payload)).fetch_jobs("acme")
    text = normalize_job(jobs[0]).description
    assert "<script" not in text and "<img" not in text


async def test_not_found():
    with pytest.raises(SourceNotFound):
        await _src(_json({}, status=404)).fetch_jobs("acme")


async def test_bad_shape():
    with pytest.raises(SourceBadResponse):
        await _src(_json([1, 2])).fetch_jobs("acme")
    with pytest.raises(SourceBadResponse):
        await _src(_json({"jobs": "x"})).fetch_jobs("acme")


async def test_unlisted_jobs_skipped():
    payload = {"jobs": [_item("1"), _item("2", isListed=False)]}
    jobs = await _src(_json(payload)).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]


async def test_is_remote_sets_hint():
    payload = {"jobs": [_item("1", workplaceType=None, isRemote=True)]}
    jobs = await _src(_json(payload)).fetch_jobs("acme")
    assert jobs[0].work_model_hint == "remote"


def test_normalize_identifier():
    src = AshbySource(None, max_bytes=1)
    assert src.normalize_identifier("https://jobs.ashbyhq.com/Ashby/abc") == "ashby"
    assert src.normalize_identifier("Acme") == "acme"
    with pytest.raises(ValueError):
        src.normalize_identifier("https://evil.test/acme")
