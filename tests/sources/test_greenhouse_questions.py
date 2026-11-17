import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse, SourceNotFound
from rumo_core.sources.greenhouse import GreenhouseSource

FIXTURE = Path(__file__).parents[1] / "fixtures/greenhouse/job_questions.json"


async def _no_sleep(_seconds):
    return None


def _source(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GreenhouseSource(client, max_bytes=10_000_000, sleep=_no_sleep)


async def test_fetch_job_questions_parses_fixture():
    body = FIXTURE.read_bytes()
    data = json.loads(body)

    def handler(request):
        assert request.url.path == "/v1/boards/anthropic/jobs/5456619008"
        assert request.url.params["questions"] == "true"
        return httpx.Response(200, content=body)

    qs = await _source(handler).fetch_job_questions("anthropic", "5456619008")
    expected = len(data["questions"]) + sum(len(b["questions"]) for b in data["compliance"])
    assert len(qs) == expected
    labels = [q.label for q in qs]
    assert "Why Anthropic?" in labels
    assert any(q.section == "compliance" for q in qs)


async def test_fetch_job_questions_404_and_bad_json():
    async def run(body, status):
        return await _source(lambda r: httpx.Response(status, content=body)).fetch_job_questions("acme", "1")

    with pytest.raises(SourceNotFound):
        await run(b"{}", 404)
    with pytest.raises(SourceBadResponse):
        await run(b"not json", 200)


async def test_external_id_must_be_digits():
    with pytest.raises(ValueError):
        await _source(lambda r: httpx.Response(200, content=b"{}")).fetch_job_questions("acme", "../x")
