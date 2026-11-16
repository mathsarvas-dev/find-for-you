import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse
from rumo_core.sources.feeds.base import FeedParams
from rumo_core.sources.feeds.himalayas import API, HimalayasFeed
from rumo_core.sources.http import SafeClient

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "himalayas" / "jobs.json"


def _feed(body: bytes, calls: list | None = None):
    def handler(request):
        if calls is not None:
            calls.append(request.headers["host"] + request.url.path)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async def resolve(host, port):
        return ["93.184.216.34"]

    async def no_sleep(seconds):
        return None

    client = SafeClient(httpx.MockTransport(handler), resolve=resolve, sleep=no_sleep,
                        clock=lambda: 0.0)
    return HimalayasFeed(client, max_bytes=20_000_000)


async def test_parses_real_fixture_keeps_application_link():
    data = json.loads(FIX.read_bytes())
    calls: list[str] = []
    items = await _feed(FIX.read_bytes(), calls).fetch(FeedParams())
    assert len(items) == len(data["jobs"])
    first = items[0]
    assert first.raw.url == data["jobs"][0]["applicationLink"]
    assert first.raw.external_id == data["jobs"][0]["guid"]
    assert first.raw.work_model_hint == "remote"
    assert first.raw.location_text.startswith("Remote - ")
    assert calls == [API.replace("https://", "")]  # first page only, no cursor


async def test_salary_only_when_complete():
    body = json.dumps({"jobs": [
        {"guid": "g1", "title": "T", "applicationLink": "https://himalayas.app/a",
         "minSalary": 100000, "maxSalary": 120000, "currency": "usd", "salaryPeriod": "annual",
         "pubDate": 1794694126, "companyName": "Acme"},
        {"guid": "g2", "title": "T", "applicationLink": "https://himalayas.app/b",
         "minSalary": 100000, "currency": "USD", "salaryPeriod": "hourly"},
    ]}).encode()
    items = await _feed(body).fetch(FeedParams())
    assert items[0].raw.salary_hint is not None
    assert items[0].raw.salary_hint.period == "year"
    assert items[0].raw.salary_hint.currency == "USD"
    assert items[1].raw.salary_hint is None


async def test_bad_shape_and_missing_fields():
    with pytest.raises(SourceBadResponse):
        await _feed(b"[]").fetch(FeedParams())
    body = json.dumps({"jobs": [{"title": "sem guid"}, {"guid": "g", "title": "T"}]}).encode()
    assert await _feed(body).fetch(FeedParams()) == []
