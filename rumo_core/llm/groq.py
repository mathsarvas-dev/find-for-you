import asyncio
import math
import re
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import ValidationError

from rumo_core.config import Settings
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMModelMissing, LLMRateLimited, T
from rumo_core.llm.quota import parse_quota
from rumo_core.llm.ratelimit import RateLimiter

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_429_ATTEMPTS = 5
MAX_VALIDATION_ATTEMPTS = 2
MAX_RETRY_AFTER_SECONDS = 60.0
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def _retry_after_value(response: httpx.Response) -> float | None:
    """Seconds from Retry-After; inf when it is present but not finite; None when absent/unparsable."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else float("inf")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    value = _retry_after_value(response)
    if value is not None:
        if value > MAX_RETRY_AFTER_SECONDS:
            raise LLMRateLimited(
                f"Groq asked to wait too long (Retry-After={response.headers.get('Retry-After')!r})"
            )
        return value
    return 2**attempt


def _error_code(response: httpx.Response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def _is_json_validate_failed(response: httpx.Response) -> bool:
    try:
        return response.json()["error"]["code"] == "json_validate_failed"
    except (ValueError, KeyError, TypeError):
        return False


class GroqProvider:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        sleep=asyncio.sleep,
        store=None,
    ):
        self._settings = settings
        # QuotaStore-like (record / mark_missing / set_next_try); None = no bookkeeping
        self._store = store
        self._client = client
        self._limiter = limiter
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._settings.groq_model

    def _note_next_try(self, seconds: float) -> None:
        if self._store is not None:
            at = datetime.now(timezone.utc) + timedelta(seconds=min(seconds, 86400.0))
            self._store.set_next_try(self.model, at)

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
            if self._store is not None and resp.status_code != 404:
                snap = parse_quota(resp.headers, datetime.now(timezone.utc))
                if resp.status_code != 429 or snap is not None:
                    self._store.record(self.model, snap)
            if resp.status_code == 404 and _error_code(resp) == "model_not_found":
                if self._store is not None:
                    self._store.mark_missing(self.model)
                raise LLMModelMissing(f"Groq model {self.model!r} not found")
            if resp.status_code == 429:
                wait = _retry_after_value(resp)
                self._note_next_try(wait if wait is not None else 2**attempt)
                if wait is not None and wait > MAX_RETRY_AFTER_SECONDS:
                    raise LLMRateLimited("Groq asked to wait too long")
                if attempt < MAX_429_ATTEMPTS - 1:
                    await self._sleep(_retry_after(resp, attempt))
                continue
            if resp.status_code == 400 and _is_json_validate_failed(resp):
                raise LLMInvalidResponse("Groq json_validate_failed")
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
            try:
                content = await self._request(messages)
                return schema.model_validate_json(_strip_fences(content))
            except (ValidationError, LLMInvalidResponse) as exc:
                last = exc
        raise LLMInvalidResponse(f"Invalid JSON from model: {last}") from last
