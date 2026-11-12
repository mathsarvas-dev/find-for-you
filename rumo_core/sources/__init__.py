from collections.abc import Callable

import httpx

from rumo_core.sources.base import (
    RawJob,
    Source,
    SourceBadResponse,
    SourceError,
    SourceNotFound,
    SourceUnavailable,
)
from rumo_core.sources.greenhouse import GreenhouseSource

DEFAULT_MAX_BYTES = 20_000_000

_REGISTRY: dict[str, Callable[[], Source]] = {}


def register_source(name: str, factory: Callable[[], Source]) -> None:
    _REGISTRY[name] = factory


def get_source(name: str) -> Source:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown source: {name!r}") from None
    return factory()


register_source(
    "greenhouse",
    lambda: GreenhouseSource(
        httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False),
        max_bytes=DEFAULT_MAX_BYTES,
    ),
)

__all__ = [
    "RawJob",
    "Source",
    "SourceError",
    "SourceNotFound",
    "SourceUnavailable",
    "SourceBadResponse",
    "GreenhouseSource",
    "get_source",
    "register_source",
]
