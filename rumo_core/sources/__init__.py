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
from rumo_core.sources.http import SafeClient
from rumo_core.sources.lever import LeverSource

_REGISTRY: dict[str, Callable[[], Source]] = {}
_CACHE: dict[str, Source] = {}
_SHARED: list[SafeClient] = []  # one SafeClient for every new connector (pools, robots, limits)


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


def shared_client() -> SafeClient:
    """Process-wide SafeClient for the connectors (built on first use)."""
    if not _SHARED:
        from rumo_core.config import get_settings

        s = get_settings()
        _SHARED.append(SafeClient(
            page_max_bytes=s.fetch_page_max_bytes, timeout=s.fetch_timeout_seconds,
        ))
    return _SHARED[0]


async def aclose_sources() -> None:
    sources = list(_CACHE.values())
    _CACHE.clear()
    for source in sources:
        await source.aclose()
    while _SHARED:
        await _SHARED.pop().aclose()


def _greenhouse_factory() -> Source:
    # settings read when the source is first built (not at import time)
    from rumo_core.config import get_settings

    s = get_settings()
    return GreenhouseSource(
        httpx.AsyncClient(
            timeout=httpx.Timeout(s.fetch_timeout_seconds), follow_redirects=False
        ),
        max_bytes=s.fetch_max_bytes,
    )


register_source("greenhouse", _greenhouse_factory)


def _lever_factory() -> Source:
    from rumo_core.config import get_settings

    return LeverSource(shared_client(), max_bytes=get_settings().fetch_max_bytes)


register_source("lever", _lever_factory)

__all__ = [
    "RawJob",
    "Source",
    "SourceError",
    "SourceNotFound",
    "SourceUnavailable",
    "SourceBadResponse",
    "GreenhouseSource",
    "LeverSource",
    "get_source",
    "shared_client",
    "register_source",
    "aclose_sources",
]
