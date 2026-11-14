"""POST /filters/parse: turn a search phrase into a filter *proposal*.

Never writes anything. The phrase is private user input: it, the LLM output
and exception messages are never logged (at most the exception class name).
"""

import asyncio
import logging
import threading
import time
from collections import deque
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.matching import _filters_or_409
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.llm.base import LLMError, LLMProvider, LLMRateLimited
from rumo_core.matching.chat_filters import (
    EMPTY_NOTE,
    MAX_TEXT,
    ParsedQuery,
    ParseInvalid,
    check_chars,
    allowed_values,
    parse_query,
)

logger = logging.getLogger(__name__)

MSG_PARSE_INVALID = (
    "Não consegui transformar essa frase em filtros. "
    "Reescreva citando país, modelo de trabalho, senioridade ou idioma."
)
MSG_LLM_BUSY = "O serviço de interpretação está ocupado. Tente em instantes."
MSG_NO_LLM = "A interpretação por frase não está configurada. Use os filtros manuais."
MSG_PARSE_LIMIT = (
    "Você usou as 20 interpretações desta hora. Use os filtros manuais ou tente mais tarde."
)
MSG_BAD_TEXT = "O texto contém caracteres inválidos."

PARSE_TIMEOUT_SECONDS = 25
MAX_TRACKED_USERS = 10_000

router = APIRouter()


class HourlyLimiter:
    """Per-process, per-user sliding window: at most `max_calls` per `window` s."""

    def __init__(self, max_calls: int = 20, window: float = 3600.0,
                 clock=time.monotonic):
        self.max_calls = max_calls
        self.window = window
        self._clock = clock
        self._stamps: dict[int, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, user_id: int, now: float) -> deque[float] | None:
        stamps = self._stamps.get(user_id)
        if stamps is None:
            return None
        while stamps and now - stamps[0] >= self.window:
            stamps.popleft()
        if not stamps:
            del self._stamps[user_id]
            return None
        return stamps

    def acquire(self, user_id: int) -> bool:
        with self._lock:
            now = self._clock()
            stamps = self._prune(user_id, now)
            if stamps is not None and len(stamps) >= self.max_calls:
                return False
            if stamps is None and len(self._stamps) >= MAX_TRACKED_USERS:
                for uid in list(self._stamps):  # bound memory: evict expired users
                    self._prune(uid, now)
            if stamps is None:
                stamps = self._stamps[user_id] = deque(maxlen=self.max_calls)
            stamps.append(now)
            return True

    def retry_after(self, user_id: int) -> int:
        with self._lock:
            now = self._clock()
            stamps = self._prune(user_id, now)
            if stamps is None or len(stamps) < self.max_calls:
                return 0
            return max(1, int(stamps[0] + self.window - now) + 1)


parse_limiter = HourlyLimiter()


def get_parse_limiter() -> HourlyLimiter:
    """Dependency (overridden in tests)."""
    return parse_limiter


@lru_cache(maxsize=1)
def _build_llm() -> LLMProvider | None:
    settings = get_settings()
    if not settings.groq_api_key:
        return None
    import httpx

    from rumo_core.llm.groq import GroqProvider
    from rumo_core.llm.ratelimit import RateLimiter

    return GroqProvider(
        settings, httpx.AsyncClient(timeout=30), RateLimiter(settings.groq_max_per_minute)
    )


def get_filters_llm() -> LLMProvider | None:
    """Dependency: one Groq provider per process; None without an API key."""
    return _build_llm()


class ParseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_TEXT)


@router.post("/filters/parse", response_model=ParsedQuery)
async def parse_filters(
    body: ParseIn,
    user_id: int = Depends(current_user_id),
    session: Session = Depends(get_session),
    llm: LLMProvider | None = Depends(get_filters_llm),
    limiter: HourlyLimiter = Depends(get_parse_limiter),
):
    current = await run_in_threadpool(_filters_or_409, session, user_id)
    text = body.text.strip()
    if not text:  # no LLM, no limit
        return ParsedQuery(notes=[EMPTY_NOTE])
    try:
        check_chars(text)
    except ValueError:
        raise HTTPException(422, MSG_BAD_TEXT) from None
    if llm is None:
        raise HTTPException(503, MSG_NO_LLM)
    if not limiter.acquire(user_id):
        raise HTTPException(
            429, MSG_PARSE_LIMIT,
            headers={"Retry-After": str(max(1, limiter.retry_after(user_id)))},
        )
    try:
        allowed = await run_in_threadpool(allowed_values, session, user_id)
        return await asyncio.wait_for(
            parse_query(llm, text, current, allowed), PARSE_TIMEOUT_SECONDS
        )
    except ValueError:
        raise HTTPException(422, MSG_BAD_TEXT) from None
    except ParseInvalid:
        raise HTTPException(422, MSG_PARSE_INVALID) from None
    except (LLMRateLimited, LLMError, TimeoutError) as exc:
        logger.warning("filters/parse LLM failure: %s", type(exc).__name__)
        raise HTTPException(503, MSG_LLM_BUSY) from None
