import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

import gzip

from rumo_core.sources import aclose_sources, get_source, register_source
from rumo_core.sources.base import (
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)
from rumo_core.sources.greenhouse import GreenhouseSource

FIX = Path(__file__).parent.parent / "fixtures" / "greenhouse"


def make(responses, max_bytes=5_000_000):
    reqs: list[httpx.Request] = []
    sleeps: list[float] = []
    it = iter(responses)

    def handler(request):
        reqs.append(request)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    async def sleep(s):
        sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GreenhouseSource(client, max_bytes=max_bytes, sleep=sleep), reqs, sleeps


def ok(obj):
    return httpx.Response(200, json=obj)


@pytest.mark.parametrize("name", ["gitlab", "anthropic"])
async def test_parses_real_fixture(name):
    data = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    src, reqs, _ = make([ok(data)])
    jobs = await src.fetch_jobs(name)
    assert str(reqs[0].url) == f"https://boards-api.greenhouse.io/v1/boards/{name}/jobs?content=true"
    assert len(jobs) == len(data["jobs"]) == 7
    for job, item in zip(jobs, data["jobs"]):
        assert job.external_id == str(item["id"])
        assert isinstance(job.external_id, str)
        assert job.title == item["title"]
        assert job.url == item["absolute_url"] and job.url.startswith("http")
        assert job.location_text == item["location"]["name"]
        assert job.department == item["departments"][0]["name"]
        assert job.posted_at is not None and job.posted_at.utcoffset() is not None
        assert "&lt;" not in job.description_html
        assert "<p>" in job.description_html or "<div" in job.description_html
        assert job.raw == item
        json.dumps(job.raw)


async def test_posted_at_prefers_first_published_and_handles_naive_and_bad():
    items = [
        {"id": 1, "title": "a", "absolute_url": "u", "first_published": "2026-01-01T10:00:00-05:00", "updated_at": "2026-02-01T00:00:00Z"},
        {"id": 2, "title": "b", "absolute_url": "u", "updated_at": "2026-02-01T00:00:00"},
        {"id": 3, "title": "c", "absolute_url": "u", "updated_at": "garbage"},
        {"id": 4, "title": "d", "absolute_url": "u"},
    ]
    src, _, _ = make([ok({"jobs": items})])
    jobs = await src.fetch_jobs("x")
    assert jobs[0].posted_at.utcoffset() == timedelta(hours=-5)
    assert jobs[0].posted_at.month == 1
    assert jobs[1].posted_at.utcoffset() == timedelta(0)
    assert jobs[2].posted_at is None and jobs[3].posted_at is None
    assert jobs[3].location_text == "" and jobs[3].department is None


async def test_skips_items_without_id_or_title():
    items = [
        {"id": 1, "title": "ok", "absolute_url": "u"},
        {"title": "no id", "absolute_url": "u"},
        {"id": 3},
        {"id": 4, "title": "", "absolute_url": "u"},
        "junk",
    ]
    src, _, _ = make([ok({"jobs": items})])
    jobs = await src.fetch_jobs("x")
    assert [j.external_id for j in jobs] == ["1"]


async def test_empty_fixture():
    data = json.loads((FIX / "empty.json").read_text())
    src, _, _ = make([ok(data)])
    assert await src.fetch_jobs("x") == []


async def test_404_is_not_found():
    src, reqs, _ = make([httpx.Response(404, text="secret body")])
    with pytest.raises(SourceNotFound) as ei:
        await src.fetch_jobs("nope")
    assert "secret" not in str(ei.value)
    assert len(reqs) == 1


async def test_other_4xx_is_bad_response_no_retry():
    src, reqs, _ = make([httpx.Response(403, text="secret body")])
    with pytest.raises(SourceBadResponse) as ei:
        await src.fetch_jobs("x")
    assert "secret" not in str(ei.value)
    assert len(reqs) == 1


async def test_5xx_retries_then_unavailable():
    src, reqs, sleeps = make([httpx.Response(503, text="secret")] * 4)
    with pytest.raises(SourceUnavailable) as ei:
        await src.fetch_jobs("x")
    assert "secret" not in str(ei.value)
    assert len(reqs) == 4
    assert len(sleeps) == 3 and sleeps == sorted(sleeps) and len(set(sleeps)) == 3


async def test_5xx_then_success():
    src, reqs, _ = make([httpx.Response(500), ok({"jobs": []})])
    assert await src.fetch_jobs("x") == []
    assert len(reqs) == 2


async def test_429_retry_after_respected_and_capped():
    src, _, sleeps = make([httpx.Response(429, headers={"Retry-After": "7"}), ok({"jobs": []})])
    await src.fetch_jobs("x")
    assert sleeps == [7.0]

    src, _, sleeps = make([httpx.Response(429, headers={"Retry-After": "9999"}), ok({"jobs": []})])
    await src.fetch_jobs("x")
    assert sleeps == [60.0]


@pytest.mark.parametrize("val", ["nan", "inf", "abc", "-5", ""])
async def test_429_bad_retry_after_falls_back(val):
    src, _, sleeps = make([httpx.Response(429, headers={"Retry-After": val}), ok({"jobs": []})])
    await src.fetch_jobs("x")
    assert len(sleeps) == 1 and 0 <= sleeps[0] <= 60


async def test_429_exhausted_is_unavailable():
    src, reqs, _ = make([httpx.Response(429)] * 4)
    with pytest.raises(SourceUnavailable):
        await src.fetch_jobs("x")
    assert len(reqs) == 4


async def test_timeout_is_unavailable():
    src, _, _ = make([httpx.ReadTimeout("slow")])
    with pytest.raises(SourceUnavailable):
        await src.fetch_jobs("x")
    src, _, _ = make([httpx.ConnectError("down")])
    with pytest.raises(SourceUnavailable):
        await src.fetch_jobs("x")


async def test_invalid_json_is_bad_response():
    text = (FIX / "malformed.json").read_text()
    src, _, _ = make([httpx.Response(200, text=text)])
    with pytest.raises(SourceBadResponse) as ei:
        await src.fetch_jobs("x")
    assert "secrettitle" not in str(ei.value)


@pytest.mark.parametrize("body", [[], {"nope": 1}, {"jobs": "x"}])
async def test_unexpected_shape_is_bad_response(body):
    src, _, _ = make([ok(body)])
    with pytest.raises(SourceBadResponse):
        await src.fetch_jobs("x")


async def test_oversized_body_is_bad_response():
    big = json.dumps({"jobs": [], "pad": "x" * 5000})
    src, _, _ = make([httpx.Response(200, text=big)], max_bytes=1000)
    with pytest.raises(SourceBadResponse):
        await src.fetch_jobs("x")


async def test_user_agent_is_generic_and_sent():
    src, reqs, _ = make([ok({"jobs": []})])
    await src.fetch_jobs("x")
    assert reqs[0].headers["user-agent"] == "Rumo/0.1 job-search-tool"
    assert reqs[0].method == "GET"


def test_normalize_identifier_variants():
    n = GreenhouseSource(httpx.AsyncClient(), max_bytes=1).normalize_identifier
    assert n("foo") == "foo"
    assert n(" Foo ") == "foo"
    assert n("https://boards.greenhouse.io/foo") == "foo"
    assert n("boards.greenhouse.io/Foo/") == "foo"
    assert n("https://job-boards.greenhouse.io/foo/jobs/123") == "foo"
    assert n("https://boards.greenhouse.io/embed/job_board?for=Foo") == "foo"
    assert n("boards.greenhouse.io/embed/job_board?for=foo") == "foo"
    assert n("my_co-1") == "my_co-1"
    for bad in [
        "", "   ", "a b", "foo!", "a/b", "https://boards.greenhouse.io/", "x" * 101,
        "https://boards.greenhouse.io/embed/job_board", "ação",
    ]:
        with pytest.raises(ValueError):
            n(bad)
    assert n("x" * 100) == "x" * 100


async def test_get_source_registry():
    try:
        assert get_source("greenhouse").name == "greenhouse"
        with pytest.raises(KeyError, match="nope"):
            get_source("nope")
        sentinel = _Dummy()
        register_source("dummy", lambda: sentinel)
        assert get_source("dummy") is sentinel
    finally:
        await aclose_sources()
        from rumo_core import sources

        sources._REGISTRY.pop("dummy", None)


class _Dummy:
    name = "dummy"

    async def aclose(self):
        pass


async def test_get_source_cached_and_aclose_resets():
    a = get_source("greenhouse")
    assert get_source("greenhouse") is a
    client = a._client
    assert not client.is_closed
    await aclose_sources()
    assert client.is_closed
    b = get_source("greenhouse")
    assert b is not a and not b._client.is_closed
    await aclose_sources()
    assert b._client.is_closed


async def test_streaming_cap_aborts_early_without_content_length():
    consumed = 0

    async def body():
        nonlocal consumed
        for _ in range(100):
            consumed += 1
            yield b"x" * 100

    src, _, _ = make([httpx.Response(200, content=body())], max_bytes=350)
    with pytest.raises(SourceBadResponse):
        await src.fetch_jobs("x")
    assert consumed < 10


async def test_gzip_decoded_size_over_cap_is_bad_response():
    raw = json.dumps({"jobs": [], "pad": "a" * 50_000}).encode()
    comp = gzip.compress(raw)
    assert len(comp) < 1000
    resp = httpx.Response(200, content=comp, headers={"Content-Encoding": "gzip"})
    src, _, _ = make([resp], max_bytes=2000)
    with pytest.raises(SourceBadResponse):
        await src.fetch_jobs("x")


async def test_skips_items_without_usable_url_or_id():
    items = [
        {"id": 1, "title": "ok", "absolute_url": "https://x/1"},
        {"id": 2, "title": "nourl", "absolute_url": ""},
        {"id": 3, "title": "nourl2"},
        {"id": "  ", "title": "blank id", "absolute_url": "https://x/4"},
        {"id": [1], "title": "list id", "absolute_url": "https://x/5"},
        {"id": "abc", "title": "str id", "absolute_url": "https://x/6"},
    ]
    src, _, _ = make([ok({"jobs": items})])
    jobs = await src.fetch_jobs("x")
    assert [j.external_id for j in jobs] == ["1", "abc"]


async def test_single_unescape_only():
    item = {"id": 1, "title": "t", "absolute_url": "https://x/1", "content": "&amp;lt;b&amp;gt; &lt;p&gt;"}
    src, _, _ = make([ok({"jobs": [item]})])
    jobs = await src.fetch_jobs("x")
    assert jobs[0].description_html == "&lt;b&gt; <p>"


async def test_3xx_is_bad_response():
    src, reqs, _ = make([httpx.Response(302, headers={"Location": "https://evil/"}, text="secret")])
    with pytest.raises(SourceBadResponse) as ei:
        await src.fetch_jobs("x")
    assert "secret" not in str(ei.value) and len(reqs) == 1


async def test_greenhouse_factory_honours_settings(monkeypatch):
    from rumo_core.config import get_settings

    monkeypatch.setenv("FETCH_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("FETCH_MAX_BYTES", "12345")
    get_settings.cache_clear()
    await aclose_sources()
    try:
        src = get_source("greenhouse")
        assert src._max_bytes == 12345
        assert src._client.timeout == httpx.Timeout(7.5)
        assert src._client.follow_redirects is False
    finally:
        await aclose_sources()
        get_settings.cache_clear()
