import asyncio

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse, SourceUnavailable
from rumo_core.sources.http import (
    USER_AGENT,
    SafeClient,
    UnsafeURL,
    canonical_url,
    is_public_ip,
    registrable_domain,
)

PUBLIC = "93.184.216.34"


class FakeResolver:
    def __init__(self, answers=None):
        self.calls: list[tuple[str, int]] = []
        self._answers = list(answers) if answers else None

    async def __call__(self, host, port):
        self.calls.append((host, port))
        if self._answers:
            return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return [PUBLIC]


def _client(handler, resolver=None, **kw):
    return SafeClient(
        httpx.MockTransport(handler), resolve=resolver or FakeResolver(), **kw
    )


JSON = {"content-type": "application/json"}
HTML = {"content-type": "text/html; charset=utf-8"}


async def test_rejects_non_http_schemes():
    seen = []
    client = _client(lambda r: seen.append(r) or httpx.Response(200, headers=JSON))
    for url in ["file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "gopher://x"]:
        with pytest.raises(UnsafeURL):
            await client.get(url)
    assert seen == []


async def test_rejects_private_and_special_ips():
    seen = []
    client = _client(lambda r: seen.append(r) or httpx.Response(200, headers=JSON))
    hosts = [
        "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "[::1]", "[fd00::1]", "[::ffff:127.0.0.1]",
    ]
    for host in hosts:
        with pytest.raises(UnsafeURL):
            await client.get(f"https://{host}/jobs")
    assert seen == []


async def test_rejects_name_resolving_to_private_ip():
    seen = []
    for answers in (["10.0.0.1"], ["93.184.216.34", "127.0.0.1"]):
        client = _client(
            lambda r: seen.append(r) or httpx.Response(200, headers=JSON),
            resolver=FakeResolver([answers]),
        )
        with pytest.raises(UnsafeURL):
            await client.get("https://jobs.example.com/api")
    assert seen == []


async def test_connects_to_checked_ip_dns_rebinding():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers=JSON, json={"ok": True})

    resolver = FakeResolver([[PUBLIC], ["127.0.0.1"]])
    client = _client(handler, resolver=resolver)
    await client.get("https://jobs.example.com/api/offers")
    assert len(resolver.calls) == 1
    req = seen[0]
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "jobs.example.com"
    assert req.extensions["sni_hostname"] == "jobs.example.com"


async def test_redirect_to_private_ip_rejected():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    client = _client(handler)
    with pytest.raises(UnsafeURL):
        await client.get("http://jobs.example.com/careers")
    assert len(seen) == 1


async def test_redirect_other_domain_rejected_same_domain_followed():
    def same(request):
        if request.headers["host"] == "jobs.example.com":
            return httpx.Response(302, headers={"location": "https://careers.example.com/vagas"})
        return httpx.Response(200, headers=HTML, content=b"<html></html>")

    res = await _client(same).get("https://jobs.example.com/")
    assert res.status == 200 and res.url == "https://careers.example.com/vagas"

    def other(request):
        return httpx.Response(302, headers={"location": "https://evil.test/x"})

    with pytest.raises(UnsafeURL):
        await _client(other).get("https://jobs.example.com/")


async def test_four_redirects_rejected():
    hops = {"n": 0}

    def handler(request):
        hops["n"] += 1
        n = hops["n"]
        return httpx.Response(302, headers={"location": f"https://jobs.example.com/{n}"})

    with pytest.raises(UnsafeURL):
        await _client(handler).get("https://jobs.example.com/")
    assert hops["n"] == 4


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com@127.0.0.1/",
        "http://user:pw@example.com/",
        "http://0x7f.1/",
        "http://2130706433/",
        "http://example.com:8080/",
    ],
)
async def test_rejects_userinfo_and_numeric_tricks(url):
    seen = []
    client = _client(lambda r: seen.append(r) or httpx.Response(200, headers=JSON))
    with pytest.raises(UnsafeURL):
        await client.get(url)
    assert seen == []


def test_canonical_url():
    assert canonical_url("  HTTPS://Jobs.Example.COM/Careers?x=1#top ") == (
        "https://jobs.example.com/Careers?x=1"
    )
    assert canonical_url("https://exämple.com/").startswith("https://xn--")


def test_is_public_ip():
    assert is_public_ip(PUBLIC)
    assert not is_public_ip("::ffff:127.0.0.1")
    assert not is_public_ip("garbage")


def test_registrable_domain():
    assert registrable_domain("jobs.acme.com.br") == "acme.com.br"
    assert registrable_domain("a.b.co.uk") == "b.co.uk"
    assert registrable_domain("acme.com") == "acme.com"
    assert registrable_domain("x.github.io") == "x.github.io"


async def test_size_cap_page_and_api():
    big = b"x" * 6_000_000
    client = _client(
        lambda r: httpx.Response(200, headers={**JSON, "Content-Length": "6000000"}, content=big)
    )
    with pytest.raises(SourceBadResponse):
        await client.get("https://jobs.example.com/api", kind="page")

    def streamed(r):
        return httpx.Response(200, headers=JSON, stream=httpx.ByteStream(big))

    with pytest.raises(SourceBadResponse):
        await _client(streamed).get("https://jobs.example.com/api", kind="page")

    res = await _client(streamed).get("https://jobs.example.com/api", kind="api", max_bytes=20_000_000)
    assert len(res.body) == 6_000_000


async def test_rejects_unexpected_content_type():
    for ctype in ("image/png", "application/octet-stream"):
        client = _client(
            lambda r, c=ctype: httpx.Response(200, headers={"content-type": c}, content=b"x")
        )
        with pytest.raises(SourceBadResponse):
            await client.get("https://jobs.example.com/a")
    ok = _client(lambda r: httpx.Response(200, headers=HTML, content=b"<p>x</p>"))
    assert (await ok.get("https://jobs.example.com/a")).content_type == "text/html"


async def test_timeout_is_unavailable():
    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(1)
            return httpx.Response(200, headers=JSON, content=b"{}")

    client = SafeClient(Slow(), resolve=FakeResolver(), timeout=0.05)
    with pytest.raises(SourceUnavailable):
        await client.get("https://jobs.example.com/a")


async def test_status_mapping():
    async def run(status):
        client = _client(lambda r: httpx.Response(status, headers=JSON, content=b"{}"))
        return await client.get("https://jobs.example.com/a")

    res = await run(404)
    assert res.status == 404
    with pytest.raises(SourceUnavailable):
        await run(500)
    with pytest.raises(SourceBadResponse):
        await run(400)


async def test_user_agent():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers=JSON, content=b"{}")

    await _client(handler).get("https://jobs.example.com/a")
    ua = seen[0].headers["user-agent"]
    assert ua.startswith("RumoJobSearch/") and ua.endswith(" (uso pessoal)")
    assert "@" not in ua and ua == USER_AGENT
