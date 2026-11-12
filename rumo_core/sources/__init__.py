"""Job sources registry.

Lifecycle: `register_source(name, factory)` stores a factory. `get_source(name)`
lazily builds ONE process-wide instance per name (cached) so HTTP pools are
shared and not leaked. Call `await aclose_sources()` on worker shutdown (or in
tests) to close every cached source and clear the cache; a later `get_source`
builds a fresh instance. Single event loop assumed.
"""

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
_CACHE: dict[str, Source] = {}


def register_source(name: str, factory: Callable[[], Source]) -> None:
    _REGISTRY[name] = factory
    _CACHE.pop(name, None)


def get_source(name: str) -> Source:
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown source: {name!r}") from None
    source = factory()
    _CACHE[name] = source
    return source


async def aclose_sources() -> None:
    sources = list(_CACHE.values())
    _CACHE.clear()
    for source in sources:
        await source.aclose()


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
    "aclose_sources",
]
