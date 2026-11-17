import pytest
from pydantic import BaseModel

from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMModelMissing, LLMRateLimited
from rumo_core.llm.fallback import FallbackLLM


class Out(BaseModel):
    ok: bool = True


class Fake:
    def __init__(self, model, result=None, exc=None):
        self.model = model
        self.result, self.exc, self.calls = result, exc, 0

    async def complete_json(self, messages, schema):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result or schema()


class Store:
    def __init__(self):
        self.used = []

    def mark_fallback_used(self, model, now=None):
        self.used.append(model)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


M = [{"role": "user", "content": "x"}]


async def test_primary_answers_fallback_untouched():
    p, f = Fake("big"), Fake("small")
    out = await FallbackLLM(p, f).complete_json(M, Out)
    assert out.ok and (p.calls, f.calls) == (1, 0)


@pytest.mark.parametrize("exc", [LLMRateLimited("q"), LLMModelMissing("gone")])
async def test_quota_exhausted_or_missing_uses_fallback(exc):
    store = Store()
    p, f = Fake("big", exc=exc), Fake("small")
    out = await FallbackLLM(p, f, store=store).complete_json(M, Out)
    assert out.ok and f.calls == 1 and store.used == ["big"]


async def test_primary_is_skipped_while_cooling_down():
    clock = Clock()
    p, f = Fake("big", exc=LLMRateLimited("q")), Fake("small")
    llm = FallbackLLM(p, f, clock=clock, cooldown_seconds=60)
    await llm.complete_json(M, Out)
    await llm.complete_json(M, Out)
    assert (p.calls, f.calls) == (1, 2)
    clock.t += 61
    await llm.complete_json(M, Out)
    assert p.calls == 2


@pytest.mark.parametrize("exc", [LLMError("boom"), LLMInvalidResponse("bad")])
async def test_other_errors_do_not_fall_back(exc):
    p, f = Fake("big", exc=exc), Fake("small")
    with pytest.raises(type(exc)):
        await FallbackLLM(p, f).complete_json(M, Out)
    assert f.calls == 0


async def test_fallback_failure_propagates():
    p = Fake("big", exc=LLMRateLimited("q"))
    f = Fake("small", exc=LLMRateLimited("also"))
    with pytest.raises(LLMRateLimited):
        await FallbackLLM(p, f).complete_json(M, Out)


async def test_without_fallback_it_is_the_primary():
    p = Fake("big", exc=LLMRateLimited("q"))
    with pytest.raises(LLMRateLimited):
        await FallbackLLM(p, None).complete_json(M, Out)
