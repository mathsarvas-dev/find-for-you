"""Feed registry (same lifecycle as `rumo_core.sources`: one cached instance per name).

Call `await aclose_feeds()` on shutdown. Attribution and the key requirement are
read from the registration, so the API can show them without building a feed.
"""

from collections.abc import Callable

from rumo_core.sources.feeds.base import Attribution, FeedItem, FeedParams, FeedSource
from rumo_core.sources.feeds.arbeitnow import ArbeitnowFeed
from rumo_core.sources.feeds.himalayas import HimalayasFeed
from rumo_core.sources.feeds.remoteok import RemoteOKFeed
from rumo_core.sources.feeds.remotive import RemotiveFeed

__all__ = [
    "Attribution", "FeedItem", "FeedParams", "FeedSource",
    "register_feed", "get_feed", "feed_names", "feed_attribution", "feed_needs_key",
    "aclose_feeds",
]

_REGISTRY: dict[str, Callable[[], FeedSource]] = {}
_META: dict[str, tuple[Attribution, bool]] = {}
_CACHE: dict[str, FeedSource] = {}


def register_feed(
    name: str, factory: Callable[[], FeedSource], *, attribution: Attribution,
    needs_key: bool = False,
) -> None:
    _REGISTRY[name] = factory
    _META[name] = (attribution, needs_key)
    _CACHE.pop(name, None)


def get_feed(name: str) -> FeedSource:
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown feed: {name!r}") from None
    feed = factory()
    _CACHE[name] = feed
    return feed


def feed_names() -> list[str]:
    return sorted(_REGISTRY)


def feed_attribution(name: str) -> Attribution | None:
    meta = _META.get(name)
    return meta[0] if meta else None


def feed_needs_key(name: str) -> bool:
    meta = _META.get(name)
    return bool(meta and meta[1])


async def aclose_feeds() -> None:
    feeds = list(_CACHE.values())
    _CACHE.clear()
    for feed in feeds:
        await feed.aclose()


def _shared(cls):
    def build():
        from rumo_core.config import get_settings
        from rumo_core.sources import shared_client

        return cls(shared_client(), max_bytes=get_settings().fetch_max_bytes)

    return build


register_feed("remotive", _shared(RemotiveFeed), attribution=RemotiveFeed.attribution)
register_feed("remoteok", _shared(RemoteOKFeed), attribution=RemoteOKFeed.attribution)
register_feed("arbeitnow", _shared(ArbeitnowFeed), attribution=ArbeitnowFeed.attribution)
register_feed("himalayas", _shared(HimalayasFeed), attribution=HimalayasFeed.attribution)
