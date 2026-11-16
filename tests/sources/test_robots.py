import httpx
import pytest

from rumo_core.sources.http import SafeClient
from rumo_core.sources.robots import (
    RobotsCache,
    RobotsDisallowed,
    parse_robots,
)

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


def test_parse_robots_cases():
    assert parse_robots(404, None).allowed("https://x.test/qualquer")
    assert parse_robots(410, b"").allowed("https://x.test/qualquer")
    for status, body in [(500, b"User-agent: *\nAllow: /"), (403, None), (401, b""), (200, None)]:
        assert not parse_robots(status, body).allowed("https://x.test/jobs")
    assert not parse_robots(200, b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x00\x00").allowed(
        "https://x.test/jobs"
    )
    rules = parse_robots(200, b"User-agent: *\nDisallow: /private")
    assert not rules.allowed("https://x.test/private/x")
    assert rules.allowed("https://x.test/jobs")
    assert not parse_robots(200, b"User-agent: RumoJobSearch\nDisallow: /").allowed(
        "https://x.test/jobs"
    )
    assert parse_robots(200, b"User-agent: *\nCrawl-delay: 5\nAllow: /").crawl_delay == 5.0
    sm = parse_robots(200, b"User-agent: *\nAllow: /\nSitemap: https://x.test/s.xml")
    assert sm.sitemaps == ("https://x.test/s.xml",)


def _site(robots_status=200, robots_body=b"User-agent: *\nAllow: /", seen=None, page_status=200):
    def handler(request):
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, headers={"content-type": "text/plain"}, content=robots_body)
        return httpx.Response(page_status, headers=HTML, content=b"<html></html>")

    return handler


async def test_page_disallowed_by_robots_never_requested():
    seen = []
    clock = Clock()
    handler = _site(robots_body=b"User-agent: *\nDisallow: /vagas/pesquisas", seen=seen)
    client = SafeClient(httpx.MockTransport(handler), resolve=_resolve, clock=clock, sleep=clock.sleep)
    with pytest.raises(RobotsDisallowed):
        await client.get("https://jobs.example.com/vagas/pesquisas")
    assert seen == ["/robots.txt"]


async def test_robots_cached_24h_and_error_ttl():
    seen = []
    clock = Clock()
    client = SafeClient(
        httpx.MockTransport(_site(seen=seen)), resolve=_resolve, clock=clock, sleep=clock.sleep,
        robots=RobotsCache(clock),
    )
    await client.get("https://jobs.example.com/a")
    await client.get("https://jobs.example.com/b")
    assert seen.count("/robots.txt") == 1
    clock.t += 86401
    await client.get("https://jobs.example.com/c")
    assert seen.count("/robots.txt") == 2

    seen.clear()
    clock2 = Clock()
    failing = SafeClient(
        httpx.MockTransport(_site(robots_status=500, seen=seen)), resolve=_resolve,
        clock=clock2, sleep=clock2.sleep,
    )
    with pytest.raises(RobotsDisallowed):
        await failing.get("https://flaky.example.com/a")
    clock2.t += 3601
    with pytest.raises(RobotsDisallowed):
        await failing.get("https://flaky.example.com/a")
    assert seen.count("/robots.txt") == 2


async def test_api_kind_skips_robots():
    seen = []
    clock = Clock()
    client = SafeClient(httpx.MockTransport(_site(seen=seen)), resolve=_resolve, clock=clock, sleep=clock.sleep)
    await client.get("https://jobs.example.com/api/x", kind="api")
    assert seen == ["/api/x"]


async def test_crawl_delay_and_min_interval():
    clock = Clock()
    times = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"},
                                  content=b"User-agent: *\nCrawl-delay: 5\nAllow: /")
        times.append(clock())
        return httpx.Response(200, headers=HTML, content=b"<p>x</p>")

    client = SafeClient(httpx.MockTransport(handler), resolve=_resolve, clock=clock, sleep=clock.sleep)
    await client.get("https://jobs.example.com/a", min_interval=2)
    await client.get("https://jobs.example.com/b", min_interval=2)
    assert times[1] - times[0] >= 5

    clock2 = Clock()
    client2 = SafeClient(httpx.MockTransport(_site()), resolve=_resolve, clock=clock2, sleep=clock2.sleep)
    await client2.get("https://slow.example.com/a", min_interval=30)
    from rumo_core.sources.base import SourceUnavailable
    with pytest.raises(SourceUnavailable):
        await client2.get("https://slow.example.com/b", min_interval=30)
