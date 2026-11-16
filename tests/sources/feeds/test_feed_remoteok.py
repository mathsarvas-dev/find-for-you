import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse
from rumo_core.sources.feeds.base import FeedParams
from rumo_core.sources.feeds.remoteok import RemoteOKFeed
from rumo_core.sources.http import SafeClient

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "remoteok" / "api.json"


def _feed(body: bytes):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async def resolve(host, port):
        return ["93.184.216.34"]

    async def no_sleep(seconds):
        return None

    client = SafeClient(httpx.MockTransport(handler), resolve=resolve, sleep=no_sleep,
                        clock=lambda: 0.0)
    return RemoteOKFeed(client, max_bytes=20_000_000)


async def test_parses_real_fixture_and_skips_legal_notice():
    data = json.loads(FIX.read_bytes())
    items = await _feed(FIX.read_bytes()).fetch(FeedParams())
    assert data[0].get("legal") is not None  # the notice is the first element
    assert len(items) == len(data) - 1
    assert all(i.raw.url.lower().startswith("https://remoteok.com/") for i in items)
    assert all(i.raw.work_model_hint == "remote" for i in items)
    assert all(i.raw.title for i in items)


async def test_legal_notice_element_skipped():
    body = json.dumps([{"last_updated": 1, "legal": "x"},
                       {"id": "9", "position": "Dev", "url": "https://remoteok.com/9",
                        "company": "Acme", "epoch": 1794556821}]).encode()
    items = await _feed(body).fetch(FeedParams())
    assert [i.raw.external_id for i in items] == ["9"]


async def test_epoch_fallback_when_no_date():
    body = json.dumps([{"id": "9", "position": "Dev", "url": "https://remoteok.com/9",
                        "epoch": "1794556821"}]).encode()
    item = (await _feed(body).fetch(FeedParams()))[0]
    assert item.raw.posted_at is not None and item.raw.posted_at.year == 2026


async def test_bad_shape():
    with pytest.raises(SourceBadResponse):
        await _feed(b'{"jobs": []}').fetch(FeedParams())
