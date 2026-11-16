import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse
from rumo_core.sources.feeds.base import FeedParams
from rumo_core.sources.feeds.remotive import API, RemotiveFeed
from rumo_core.sources.http import SafeClient

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "remotive" / "remote-jobs.json"


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
    return RemotiveFeed(client, max_bytes=20_000_000)


async def test_parses_real_fixture():
    data = json.loads(FIX.read_bytes())
    calls: list[str] = []
    items = await _feed(FIX.read_bytes(), calls).fetch(FeedParams())
    assert len(items) == len(data["jobs"])
    first = items[0]
    assert first.raw.external_id == str(data["jobs"][0]["id"])
    assert first.raw.url.startswith("https://remotive.com/")
    assert first.raw.work_model_hint == "remote"
    assert first.employer_name == data["jobs"][0]["company_name"].strip()
    assert calls == [API.replace("https://", "")]


async def test_bad_shape():
    with pytest.raises(SourceBadResponse):
        await _feed(b"[]").fetch(FeedParams())
    with pytest.raises(SourceBadResponse):
        await _feed(b'{"jobs": "x"}').fetch(FeedParams())


async def test_items_without_id_or_title_skipped():
    body = json.dumps({"jobs": [
        {"id": 1, "title": "a", "url": "https://remotive.com/1"},
        {"id": 2},
        {"title": "t", "url": "https://remotive.com/3"},
        {"id": 4, "title": "", "url": "https://remotive.com/4"},
        5,
    ]}).encode()
    items = await _feed(body).fetch(FeedParams())
    assert [i.raw.external_id for i in items] == ["1"]


async def test_salary_text_becomes_salary_line():
    body = json.dumps({"jobs": [{"id": 1, "title": "a", "url": "https://remotive.com/1",
                                 "salary": "$35k - $52k", "description": "<p>x</p>"}]}).encode()
    items = await _feed(body).fetch(FeedParams())
    assert items[0].raw.description_html.startswith("<p>Salary: $35k")
