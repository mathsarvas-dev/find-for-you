import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse
from rumo_core.sources.feeds.arbeitnow import API, ArbeitnowFeed
from rumo_core.sources.feeds.base import FeedParams
from rumo_core.sources.http import SafeClient

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "arbeitnow" / "job-board-api.json"


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
    return ArbeitnowFeed(client, max_bytes=20_000_000)


async def test_parses_real_fixture():
    data = json.loads(FIX.read_bytes())
    calls: list[str] = []
    items = await _feed(FIX.read_bytes(), calls).fetch(FeedParams())
    assert len(items) == len(data["data"])
    assert items[0].raw.external_id == data["data"][0]["slug"]
    assert items[0].raw.posted_at is not None and items[0].raw.posted_at.tzinfo is not None
    assert calls == [API.replace("https://", "")]  # first page only


async def test_remote_true_sets_hint():
    body = json.dumps({"data": [
        {"slug": "a", "title": "T", "url": "https://www.arbeitnow.com/a", "remote": True,
         "created_at": 1794693685, "company_name": "Acme"},
        {"slug": "b", "title": "T", "url": "https://www.arbeitnow.com/b", "remote": False,
         "created_at": 1794693685},
    ]}).encode()
    items = await _feed(body).fetch(FeedParams())
    assert items[0].raw.work_model_hint == "remote"
    assert items[1].raw.work_model_hint is None


async def test_bad_shape():
    with pytest.raises(SourceBadResponse):
        await _feed(b"[]").fetch(FeedParams())
