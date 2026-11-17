"""Automatic fallback model for NON-CRITICAL background tasks only.

The job-field fill wraps its provider in FallbackLLM: when the primary model is out
of quota (429 that persisted) or gone (404 model_not_found), the call is answered by
a second model with its own quota. Critical, user-facing work (resume import,
interview, matching, applications) never gets this wrapper: it keeps its model
and waits instead of silently switching to a weaker one.
"""

import time

from rumo_core.llm.base import LLMModelMissing, LLMProvider, LLMRateLimited, T

RATE_COOLDOWN_SECONDS = 120.0
MISSING_COOLDOWN_SECONDS = 900.0


class FallbackLLM:
    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None,
        *,
        store=None,
        clock=time.monotonic,
        cooldown_seconds: float = RATE_COOLDOWN_SECONDS,
    ):
        self._primary = primary
        self._fallback = fallback
        self._store = store
        self._clock = clock
        self._cooldown = cooldown_seconds
        self._blocked_until = 0.0

    @property
    def model(self) -> str | None:
        return getattr(self._primary, "model", None)

    def _note(self) -> None:
        if self._store is not None:
            self._store.mark_fallback_used(self.model or "")

    async def complete_json(self, messages: list[dict[str, str]], schema: type[T]) -> T:
        if self._fallback is None:
            return await self._primary.complete_json(messages, schema)
        if self._clock() < self._blocked_until:
            self._note()
            return await self._fallback.complete_json(messages, schema)
        try:
            return await self._primary.complete_json(messages, schema)
        except (LLMRateLimited, LLMModelMissing) as exc:
            missing = isinstance(exc, LLMModelMissing)
            self._blocked_until = self._clock() + (
                MISSING_COOLDOWN_SECONDS if missing else self._cooldown
            )
            self._note()
            return await self._fallback.complete_json(messages, schema)
