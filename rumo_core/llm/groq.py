import asyncio
import re

import httpx
from pydantic import ValidationError

from rumo_core.config import Settings
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMRateLimited, T
from rumo_core.llm.ratelimit import RateLimiter

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_429_ATTEMPTS = 5
MAX_VALIDATION_ATTEMPTS = 2
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def _retry_after(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw is not None:
        try:
            value = float(raw.strip())
            if value >= 0:
                return value
        except ValueError:
            pass
    return 2**attempt


class GroqProvider:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        sleep=asyncio.sleep,
    ):
        self._settings = settings
        self._client = client
        self._limiter = limiter
        self._sleep = sleep

    async def _request(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self._settings.groq_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._settings.groq_api_key}"}
        for attempt in range(MAX_429_ATTEMPTS):
            await self._limiter.acquire()
            try:
                resp = await self._client.post(GROQ_URL, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise LLMError(f"Groq request failed: {exc}") from exc
            if resp.status_code == 429:
                if attempt < MAX_429_ATTEMPTS - 1:
                    await self._sleep(_retry_after(resp, attempt))
                continue
            if resp.status_code != 200:
                raise LLMError(f"Groq returned HTTP {resp.status_code}")
            try:
                return resp.json()["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMInvalidResponse("Unexpected Groq response shape") from exc
        raise LLMRateLimited("Groq rate limit persisted after retries")

    async def complete_json(self, messages: list[dict[str, str]], schema: type[T]) -> T:
        if not self._settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set")
        last: Exception | None = None
        for _ in range(MAX_VALIDATION_ATTEMPTS):
            content = await self._request(messages)
            try:
                return schema.model_validate_json(_strip_fences(content))
            except ValidationError as exc:
                last = exc
        raise LLMInvalidResponse(f"Invalid JSON from model: {last}") from last
