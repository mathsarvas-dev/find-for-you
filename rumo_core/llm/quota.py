"""Per-model Groq quota meter, read from the rate-limit response headers.

Groq sends x-ratelimit-{limit,remaining,reset}-{requests,tokens} on every
answer (requests = per day, tokens = per minute). The provider hands each answer
here; QuotaStore keeps the last reading per model in `llm_model_status`, which the
API shows in Configurações. Recording never raises: a database hiccup must not
fail an LLM call.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert

from rumo_core.models import LlmModelStatus

logger = logging.getLogger("llm.quota")

_UNIT = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration(raw: str | None) -> float | None:
    """Groq reset values: '7.66s', '2m59.56s', '1h2m3s', '250ms' (or plain seconds)."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    pos, total = 0, 0.0
    for m in _UNIT.finditer(text):
        if m.start() != pos:
            return None
        total += float(m.group(1)) * _SECONDS[m.group(2)]
        pos = m.end()
    return total if pos == len(text) else None


def _int(raw: str | None) -> int | None:
    try:
        return int(raw.strip()) if raw is not None else None
    except ValueError:
        return None


@dataclass(frozen=True)
class QuotaSnapshot:
    limit_requests: int | None
    remaining_requests: int | None
    reset_requests_at: datetime | None
    limit_tokens: int | None
    remaining_tokens: int | None
    reset_tokens_at: datetime | None


def parse_quota(headers, now: datetime) -> QuotaSnapshot | None:
    """None when the answer carried no rate-limit headers at all."""
    get = headers.get

    def at(name: str) -> datetime | None:
        secs = parse_duration(get(name))
        return now + timedelta(seconds=secs) if secs is not None else None

    snap = QuotaSnapshot(
        limit_requests=_int(get("x-ratelimit-limit-requests")),
        remaining_requests=_int(get("x-ratelimit-remaining-requests")),
        reset_requests_at=at("x-ratelimit-reset-requests"),
        limit_tokens=_int(get("x-ratelimit-limit-tokens")),
        remaining_tokens=_int(get("x-ratelimit-remaining-tokens")),
        reset_tokens_at=at("x-ratelimit-reset-tokens"),
    )
    if all(v is None for v in vars(snap).values()):
        return None
    return snap


def quota_exhausted(snap: QuotaSnapshot | None, now: datetime) -> bool:
    """True while a window with nothing left has not reset yet."""
    if snap is None:
        return False
    for remaining, reset in (
        (snap.remaining_requests, snap.reset_requests_at),
        (snap.remaining_tokens, snap.reset_tokens_at),
    ):
        if remaining is not None and remaining <= 0 and (reset is None or reset > now):
            return True
    return False


class QuotaStore:
    """Writes the per-model status row (upsert). Every method is best effort."""

    def __init__(self, session_factory):
        self._sf = session_factory

    def _upsert(self, model: str, values: dict) -> None:
        try:
            stmt = insert(LlmModelStatus).values(model=model, **values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[LlmModelStatus.model], set_=values
            )
            with self._sf() as s:
                s.execute(stmt)
                s.commit()
        except Exception as exc:  # noqa: BLE001 - never fail an LLM call over bookkeeping
            logger.warning("could not record LLM status (%s)", type(exc).__name__)

    def record(self, model: str, snap: QuotaSnapshot | None, now: datetime | None = None) -> None:
        """An answer arrived: the model exists, the wait (if any) is over."""
        now = now or _now()
        values: dict = {
            "exists": True, "missing_since": None, "observed_at": now, "next_try_at": None,
        }
        if snap is not None:
            values.update(vars(snap))
        self._upsert(model, values)

    def mark_missing(self, model: str, now: datetime | None = None) -> None:
        now = now or _now()
        since = now
        try:
            with self._sf() as s:
                row = s.get(LlmModelStatus, model)
                if row is not None and row.missing_since:
                    since = row.missing_since
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read LLM status (%s)", type(exc).__name__)
        self._upsert(model, {"exists": False, "missing_since": since, "observed_at": now})

    def set_next_try(self, model: str, at: datetime, now: datetime | None = None) -> None:
        self._upsert(model, {"next_try_at": at, "observed_at": now or _now()})

    def mark_fallback_used(self, model: str, now: datetime | None = None) -> None:
        self._upsert(model, {"fallback_used_at": now or _now()})
