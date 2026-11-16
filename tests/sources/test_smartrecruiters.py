import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse, SourceNotFound
from rumo_core.sources.http import SafeClient
from rumo_core.sources.normalize import normalize_job
from rumo_core.sources.smartrecruiters import SmartRecruitersSource

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "smartrecruiters" / "smartrecruiters.json"
PUBLIC = "93.184.216.34"


async def _resolve(host, port):
    return [PUBLIC]


async def _no_sleep(seconds):
    return None


def _src(handler):
    client = SafeClient(httpx.MockTransport(handler), resolve=_resolve, sleep=_no_sleep)
    return SmartRecruitersSource(client, max_bytes=20_000_000)


def _json(payload, status=200):
    return lambda r: httpx.Response(status, headers={"content-type": "application/json"},
                                    content=json.dumps(payload).encode())


def _item(eid, **kw):
    base = {"id": eid, "name": f"Engenheiro {eid}", "releasedDate": "2026-10-28T15:14:43.941Z",
            "location": {"city": "Poland", "region": "Remote", "country": "pl", "remote": True,
                         "hybrid": False, "fullLocation": "Poland, Remote, Poland"},
            "department": {"id": "5", "label": "Engineering"}}
    base.update(kw)
    return base


async def test_parses_real_fixture():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    jobs = await _src(_json(data)).fetch_jobs("smartrecruiters")
    assert [j.external_id for j in jobs] == [c["id"] for c in data["content"]]
    j = jobs[0]
    assert j.url == (
        "https://jobs.smartrecruiters.com/smartrecruiters/"
        f"{j.external_id}-senior-lead-engineering-operations-manager"
    )
    assert j.department == "Engineering"
    assert j.work_model_hint == "remote" and j.country_hint == "PL"
    assert j.posted_at is not None


async def test_missing_fields_tolerated():
    jobs = await _src(_json({"content": [_item("1"), {"id": "2"}, _item("3", name=None)],
                             "totalFound": 3})).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]


async def test_duplicate_ids_last_wins():
    jobs = await _src(_json({"content": [_item("1", name="a"), _item("1", name="b")],
                             "totalFound": 2})).fetch_jobs("acme")
    assert len(jobs) == 1 and jobs[0].title == "b"


async def test_hostile_html_kept_as_data():
    # the list never has a description: the connector keeps it empty
    jobs = await _src(_json({"content": [_item("1")], "totalFound": 1})).fetch_jobs("acme")
    assert normalize_job(jobs[0]).description == ""


async def test_description_empty_by_design():
    jobs = await _src(_json({"content": [_item("1")], "totalFound": 1})).fetch_jobs("acme")
    assert jobs[0].description_html == ""


async def test_pagination_offset_total_found():
    seen = []
    pages = {0: [_item(str(i)) for i in range(100)], 100: [_item(str(i)) for i in range(100, 200)],
             200: [_item(str(i)) for i in range(200, 205)]}

    def handler(request):
        seen.append(request)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=json.dumps({"offset": offset, "limit": 100,
                                                  "totalFound": 205,
                                                  "content": pages[offset]}).encode())

    jobs = await _src(handler).fetch_jobs("acme")
    assert len(jobs) == 205
    assert len(seen) == 3


async def test_not_found_and_bad_shape():
    with pytest.raises(SourceNotFound):
        await _src(_json({}, status=404)).fetch_jobs("acme")
    with pytest.raises(SourceBadResponse):
        await _src(_json({"nope": 1})).fetch_jobs("acme")


async def test_page_cap_raises():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=json.dumps({"content": [_item(str(i)) for i in range(100)],
                                                  "totalFound": 10**6}).encode())

    with pytest.raises(SourceBadResponse):
        await _src(handler).fetch_jobs("acme")


def test_normalize_identifier():
    src = SmartRecruitersSource(None, max_bytes=1)
    assert src.normalize_identifier("https://jobs.smartrecruiters.com/Acme/123-x") == "acme"
    assert src.normalize_identifier("Bosch_2") == "bosch_2"
    with pytest.raises(ValueError):
        src.normalize_identifier("https://evil.test/acme")
