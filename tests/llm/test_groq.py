import json

import httpx
import pytest
from pydantic import BaseModel

from rumo_core.config import Settings
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMRateLimited
from rumo_core.llm.groq import GroqProvider


class Out(BaseModel):
    name: str
    score: int


GOOD = '{"name":"a","score":3}'


def body(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class NoLimit:
    async def acquire(self) -> None:
        pass


def make(responses, key="k"):
    reqs: list[httpx.Request] = []
    sleeps: list[float] = []
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        reqs.append(request)
        return next(it)

    async def sleep(s):
        sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(database_url="x", groq_api_key=key)
    p = GroqProvider(settings, client, NoLimit(), sleep=sleep)
    return p, reqs, sleeps


MSGS = [{"role": "user", "content": "hi"}]


async def test_valid_json_returns_model():
    p, reqs, _ = make([httpx.Response(200, json=body(GOOD))], key="secret")
    out = await p.complete_json(MSGS, Out)
    assert out == Out(name="a", score=3)
    r = reqs[0]
    assert str(r.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert r.headers["Authorization"] == "Bearer secret"
    assert json.loads(r.content)["response_format"] == {"type": "json_object"}


async def test_accepts_markdown_fenced_json():
    p, _, _ = make([httpx.Response(200, json=body(f"```json\n{GOOD}\n```"))])
    assert await p.complete_json(MSGS, Out) == Out(name="a", score=3)


async def test_invalid_json_retries_once_then_raises():
    p, reqs, _ = make([httpx.Response(200, json=body("nope"))] * 2)
    with pytest.raises(LLMInvalidResponse):
        await p.complete_json(MSGS, Out)
    assert len(reqs) == 2


async def test_invalid_then_valid_succeeds():
    p, reqs, _ = make([httpx.Response(200, json=body("nope")), httpx.Response(200, json=body(GOOD))])
    assert await p.complete_json(MSGS, Out) == Out(name="a", score=3)
    assert len(reqs) == 2


async def test_429_respects_retry_after():
    p, _, sleeps = make([httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json=body(GOOD))])
    await p.complete_json(MSGS, Out)
    assert sleeps == [2]


async def test_429_without_retry_after_uses_backoff():
    p, _, sleeps = make(
        [
            httpx.Response(429),
            httpx.Response(429, headers={"Retry-After": "abc"}),
            httpx.Response(429),
            httpx.Response(200, json=body(GOOD)),
        ]
    )
    await p.complete_json(MSGS, Out)
    assert sleeps == [1, 2, 4]


async def test_429_exhausted_raises():
    p, reqs, _ = make([httpx.Response(429)] * 5)
    with pytest.raises(LLMRateLimited):
        await p.complete_json(MSGS, Out)
    assert len(reqs) == 5


async def test_missing_api_key_raises_without_request():
    p, reqs, _ = make([], key="")
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        await p.complete_json(MSGS, Out)
    assert reqs == []
