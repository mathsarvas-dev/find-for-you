from datetime import datetime, timezone
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


def _jvf():
    return httpx.Response(
        400, json={"error": {"code": "json_validate_failed", "message": "bad json"}}
    )


async def test_json_validate_failed_400_then_valid_succeeds():
    p, reqs, _ = make([_jvf(), httpx.Response(200, json=body(GOOD))])
    assert await p.complete_json(MSGS, Out) == Out(name="a", score=3)
    assert len(reqs) == 2


async def test_json_validate_failed_twice_raises_invalid_response():
    p, reqs, _ = make([_jvf(), _jvf()])
    with pytest.raises(LLMInvalidResponse):
        await p.complete_json(MSGS, Out)
    assert len(reqs) == 2


async def test_other_400_raises_llm_error_without_retry():
    p, reqs, _ = make([httpx.Response(400, json={"error": {"code": "other"}})])
    with pytest.raises(LLMError) as ei:
        await p.complete_json(MSGS, Out)
    assert not isinstance(ei.value, LLMInvalidResponse)
    assert len(reqs) == 1


async def test_500_raises_llm_error():
    p, reqs, _ = make([httpx.Response(500, text="boom")])
    with pytest.raises(LLMError):
        await p.complete_json(MSGS, Out)
    assert len(reqs) == 1


async def test_transport_error_raises_llm_error():
    def handler(request):
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = GroqProvider(Settings(database_url="x", groq_api_key="k"), client, NoLimit())
    with pytest.raises(LLMError):
        await p.complete_json(MSGS, Out)


@pytest.mark.parametrize("value", ["inf", "nan", "3600", "60.5"])
async def test_retry_after_too_large_raises_rate_limited_without_sleep(value):
    p, reqs, sleeps = make([httpx.Response(429, headers={"Retry-After": value})])
    with pytest.raises(LLMRateLimited):
        await p.complete_json(MSGS, Out)
    assert sleeps == []
    assert len(reqs) == 1


async def test_retry_after_60_sleeps_60():
    p, _, sleeps = make(
        [
            httpx.Response(429, headers={"Retry-After": "60"}),
            httpx.Response(200, json=body(GOOD)),
        ]
    )
    await p.complete_json(MSGS, Out)
    assert sleeps == [60.0]


class FakeStore:
    def __init__(self):
        self.calls = []

    def record(self, model, snap, now=None):
        self.calls.append(("record", model, snap))

    def mark_missing(self, model, now=None):
        self.calls.append(("missing", model))

    def set_next_try(self, model, at, now=None):
        self.calls.append(("next_try", model, at))


def make_store(responses):
    store = FakeStore()
    p, reqs, sleeps = make(responses)
    p._store = store
    return p, store


async def test_records_quota_headers_of_each_answer():
    headers = {"x-ratelimit-remaining-tokens": "123", "x-ratelimit-limit-tokens": "8000"}
    p, store = make_store([httpx.Response(200, json=body(GOOD), headers=headers)])
    await p.complete_json(MSG_USER, Out)
    kind, model, snap = store.calls[0]
    assert (kind, model) == ("record", p.model)
    assert snap.remaining_tokens == 123 and snap.limit_tokens == 8000


async def test_model_not_found_marks_missing_and_raises_specific_error():
    from rumo_core.llm.base import LLMModelMissing

    err = {"error": {"code": "model_not_found", "message": "gone"}}
    p, store = make_store([httpx.Response(404, json=err)])
    with pytest.raises(LLMModelMissing):
        await p.complete_json(MSG_USER, Out)
    assert ("missing", p.model) in store.calls


async def test_other_404_is_a_plain_llm_error():
    from rumo_core.llm.base import LLMModelMissing

    p, store = make_store([httpx.Response(404, json={"error": {"code": "other"}})])
    with pytest.raises(LLMError) as ei:
        await p.complete_json(MSG_USER, Out)
    assert not isinstance(ei.value, LLMModelMissing)
    assert not any(c[0] == "missing" for c in store.calls)


async def test_rate_limit_records_next_try_from_retry_after():
    r429 = httpx.Response(429, headers={"Retry-After": "7"})
    p, store = make_store([r429] * 5)
    with pytest.raises(LLMRateLimited):
        await p.complete_json(MSG_USER, Out)
    nexts = [c for c in store.calls if c[0] == "next_try"]
    assert nexts and nexts[-1][1] == p.model


async def test_retry_after_too_long_still_records_next_try():
    p, store = make_store([httpx.Response(429, headers={"Retry-After": "600"})])
    with pytest.raises(LLMRateLimited):
        await p.complete_json(MSG_USER, Out)
    kind, _, at = [c for c in store.calls if c[0] == "next_try"][-1]
    assert (at - datetime.now(timezone.utc)).total_seconds() > 500


def test_model_property_follows_settings():
    settings = Settings(database_url="x", groq_api_key="k", groq_model="m/x")
    p = GroqProvider(settings, httpx.AsyncClient(), NoLimit())
    assert p.model == "m/x"


MSG_USER = [{"role": "user", "content": "hi"}]
