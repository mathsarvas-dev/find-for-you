import asyncio
import json

import httpx
import pytest

from rumo_core.discovery.validate import Outcome, validate_candidate
from rumo_core.sources.greenhouse import GreenhouseSource


def _source(handler):
    async def sleep(_s):
        pass

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GreenhouseSource(client, max_bytes=1_000_000, sleep=sleep)


def _job(i, company_name=None):
    item = {"id": i, "title": f"Engineer {i}",
            "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
            "location": {"name": "Remote"}, "content": "<p>x</p>"}
    if company_name is not None:
        item["company_name"] = company_name
    return item


def _json(obj, status=200):
    return lambda request: httpx.Response(status, content=json.dumps(obj).encode())


@pytest.mark.parametrize("handler,expected", [
    (_json({"jobs": [_job(1, "Acme Inc"), _job(2, "Acme Inc")]}),
     Outcome("valid", 2, "Acme Inc")),
    (_json({"jobs": [_job(1)]}), Outcome("valid", 1, None)),
    (_json({"jobs": []}), Outcome("invalid", 0, None)),
    (_json({"error": "nope"}, 404), Outcome("invalid", 0, None, "SourceNotFound")),
    (_json({}, 429), Outcome("transient", 0, None, "SourceUnavailable")),
    (_json({}, 500), Outcome("transient", 0, None, "SourceUnavailable")),
    (lambda r: httpx.Response(200, content=b"{not json"),
     Outcome("transient", 0, None, "SourceBadResponse")),
], ids=["200-jobs-named", "200-jobs", "200-empty", "404", "429", "500", "bad-json"])
async def test_validate_candidate_outcomes(handler, expected):
    src = _source(handler)
    try:
        assert await validate_candidate(src, "acme") == expected
    finally:
        await src.aclose()


async def test_validate_candidate_network_error_and_timeout_are_transient():
    def boom(request):
        raise httpx.ConnectError("down")

    src = _source(boom)
    out = await validate_candidate(src, "acme")
    assert out.kind == "transient" and out.error == "SourceUnavailable"
    await src.aclose()

    class Slow:
        name = "slow"

        async def fetch_jobs(self, identifier):
            await asyncio.sleep(5)

    out = await validate_candidate(Slow(), "acme", timeout=0.01)
    assert out == Outcome("transient", 0, None, "TimeoutError")


async def test_validate_candidate_invalid_token_is_invalid():
    src = _source(_json({"jobs": [_job(1)]}))
    out = await validate_candidate(src, "not a token")
    assert out.kind == "invalid" and out.error == "ValueError"
    await src.aclose()


async def test_validate_candidate_name_is_bounded():
    src = _source(_json({"jobs": [_job(1, "  " + "x" * 400 + "  ")]}))
    out = await validate_candidate(src, "acme")
    assert out.kind == "valid" and out.name == "x" * 255
    await src.aclose()


async def test_validate_requires_accepted_job():
    src = _source(_json({"jobs": [_job(1), _job(2)]}))
    refused = await validate_candidate(src, "acme", accept=lambda r: False)
    assert refused == Outcome("invalid", 0, None)
    one = await validate_candidate(src, "acme", accept=lambda r: r.external_id == "1")
    assert one.kind == "valid" and one.open_jobs == 1
    await src.aclose()
