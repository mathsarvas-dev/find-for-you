import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse, SourceNotFound
from rumo_core.sources.breezy import BreezySource
from rumo_core.sources.http import SafeClient
from rumo_core.sources.normalize import normalize_job

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "breezy" / "breezy.json"
PUBLIC = "93.184.216.34"


async def _resolve(host, port):
    return [PUBLIC]


async def _no_sleep(seconds):
    return None


def _src(handler):
    client = SafeClient(httpx.MockTransport(handler), resolve=_resolve, sleep=_no_sleep)
    return BreezySource(client, max_bytes=20_000_000)


def _json(payload, status=200):
    return lambda r: httpx.Response(status, headers={"content-type": "application/json"},
                                    content=json.dumps(payload).encode())


def _item(eid, **kw):
    base = {"id": eid, "friendly_id": f"{eid}-vaga", "name": f"Vaga {eid}",
            "url": f"https://acme.breezy.hr/p/{eid}-vaga",
            "published_date": "2024-02-15T14:37:22.684Z",
            "location": {"name": "Remote, US", "is_remote": True}, "department": "Eng",
            "salary": None}
    base.update(kw)
    return base


async def test_parses_real_fixture():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    jobs = await _src(_json(data)).fetch_jobs("breezy")
    assert [j.external_id for j in jobs] == [x["id"] for x in data]
    first = jobs[0]
    assert first.url.startswith("https://breezy.breezy.hr/p/")
    assert first.location_text == "Chaos, FL"
    assert first.description_html.startswith("<p>Salary: ")


async def test_missing_fields_tolerated():
    jobs = await _src(_json([_item("1"), {"id": "2"}, 5, _item("3", name="")])).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]


async def test_duplicate_ids_last_wins():
    jobs = await _src(_json([_item("1", name="a"), _item("1", name="b")])).fetch_jobs("acme")
    assert len(jobs) == 1 and jobs[0].title == "b"


async def test_hostile_html_kept_as_data():
    jobs = await _src(_json([_item("1", salary="<script>x()</script><img onerror=y()>")])).fetch_jobs("acme")
    text = normalize_job(jobs[0]).description
    assert "<script" not in text and "<img" not in text


async def test_salary_text_feeds_salary_extraction():
    jobs = await _src(_json([_item("1", salary="$100,000 - $120,000")])).fetch_jobs("acme")
    n = normalize_job(jobs[0])
    assert n.salary_known is True
    assert n.salary_currency == "USD"


async def test_not_found_and_bad_shape():
    with pytest.raises(SourceNotFound):
        await _src(_json({}, status=404)).fetch_jobs("acme")
    with pytest.raises(SourceBadResponse):
        await _src(_json({"jobs": []})).fetch_jobs("acme")


def test_normalize_identifier():
    src = BreezySource(None, max_bytes=1)
    assert src.normalize_identifier("https://Acme.breezy.hr/p/123-vaga") == "acme"
    assert src.normalize_identifier("acme-2") == "acme-2"
    with pytest.raises(ValueError):
        src.normalize_identifier("https://evil.test/acme")
    with pytest.raises(ValueError):
        src.normalize_identifier("bad_id!")
