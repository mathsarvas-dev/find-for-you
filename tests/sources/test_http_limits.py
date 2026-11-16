from datetime import date, timedelta

import httpx
import pytest

from rumo_core.sources.base import SourceBlocked, SourceUnavailable
from rumo_core.sources.http import HostCapReached, SafeClient

PUBLIC = "93.184.216.34"
HTML = {"content-type": "text/html; charset=utf-8"}


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


async def _resolve(host, port):
    return [PUBLIC]


def _client(responses, clock, *, today=None, seen=None):
    """`responses`: list of status/headers for page requests, in order (robots is 404)."""
    queue = list(responses)

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if seen is not None:
            seen.append(request.url.path)
        status, headers = queue.pop(0) if len(queue) > 1 else queue[0]
        if status == 200:
            return httpx.Response(200, headers=HTML, content=b"<p>x</p>")
        return httpx.Response(status, headers=headers)

    kw = {"today": today} if today else {}
    return SafeClient(
        httpx.MockTransport(handler), resolve=_resolve, clock=clock, sleep=clock.sleep, **kw
    )


async def test_repeated_403_429_blocks():
    for pair in ([403, 403], [429, 429]):
        clock = Clock()
        client = _client([(s, {}) for s in pair], clock)
        with pytest.raises(SourceUnavailable):
            await client.get("https://jobs.example.com/a", kind="api")
        with pytest.raises(SourceBlocked) as info:
            await client.get("https://jobs.example.com/a", kind="api")
        assert info.value.host == "jobs.example.com"


async def test_403_200_403_never_blocks():
    clock = Clock()
    client = _client([(403, {}), (200, {}), (403, {})], clock)
    with pytest.raises(SourceUnavailable):
        await client.get("https://jobs.example.com/a", kind="api")
    await client.get("https://jobs.example.com/a", kind="api")
    with pytest.raises(SourceUnavailable) as info:
        await client.get("https://jobs.example.com/a", kind="api")
    assert not isinstance(info.value, SourceBlocked)


async def test_retry_after_respected():
    clock = Clock()
    seen = []
    client = _client([(429, {"Retry-After": "30"})], clock, seen=seen)
    with pytest.raises(SourceUnavailable):
        await client.get("https://jobs.example.com/a", kind="api")
    assert len(seen) == 1
    with pytest.raises(SourceUnavailable):
        await client.get("https://jobs.example.com/a", kind="api")
    assert len(seen) == 1  # no request while Retry-After is running


async def test_daily_cap():
    clock = Clock()
    day = {"d": date(2026, 11, 16)}
    seen = []
    client = _client([(200, {})], clock, today=lambda: day["d"], seen=seen)
    for _ in range(3):
        await client.get("https://jobs.example.com/a", kind="api", daily_cap=3)
    with pytest.raises(HostCapReached):
        await client.get("https://jobs.example.com/a", kind="api", daily_cap=3)
    assert len(seen) == 3
    day["d"] = day["d"] + timedelta(days=1)
    clock.t += 3600
    await client.get("https://jobs.example.com/a", kind="api", daily_cap=3)
    assert len(seen) == 4


async def test_two_hosts_do_not_share_limits():
    clock = Clock()
    seen = []
    client = _client([(200, {})], clock, seen=seen)
    await client.get("https://a.example.com/x", kind="api", min_interval=30)
    with pytest.raises(SourceUnavailable):
        await client.get("https://a.example.com/y", kind="api", min_interval=30)
    await client.get("https://b.example.com/x", kind="api", min_interval=30)
    assert len(seen) == 2
