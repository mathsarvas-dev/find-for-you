"""Validate a discovery candidate by fetching its board through the connector."""

import asyncio
from dataclasses import dataclass
from typing import Literal

from rumo_core.sources.base import (
    Source,
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_NAME_LEN = 255


@dataclass(frozen=True)
class Outcome:
    """Result of one validation.

    kind: valid (>= 1 open job), invalid (board missing or no open jobs) or
    transient (try again later). error: exception class name only, never a
    message (it may echo a response body); None for a clean answer.
    """

    kind: Literal["valid", "invalid", "transient"]
    open_jobs: int
    name: str | None
    error: str | None = None


def _company_name(raws) -> str | None:
    for r in raws:
        value = r.raw.get("company_name") if isinstance(r.raw, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_NAME_LEN]
    return None


async def validate_candidate(
    source: Source, token: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> Outcome:
    """Fetch the board once and classify it. Never raises for expected
    connector failures; uses only the given source (no other host, no LLM)."""
    try:
        raws = await asyncio.wait_for(source.fetch_jobs(token), timeout=timeout)
    except SourceNotFound as exc:
        return Outcome("invalid", 0, None, type(exc).__name__)
    except ValueError as exc:  # token rejected by normalize_identifier
        return Outcome("invalid", 0, None, type(exc).__name__)
    except (SourceUnavailable, SourceBadResponse, TimeoutError) as exc:
        return Outcome("transient", 0, None, type(exc).__name__)
    if not raws:
        return Outcome("invalid", 0, None)
    return Outcome("valid", len(raws), _company_name(raws))
