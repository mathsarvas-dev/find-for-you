"""robots.txt rules for page fetches (cached per origin).

Policy: 404/410 allow everything; any other status, unreadable or binary
content, timeouts and network errors deny everything. Errors are cached for a
shorter time so the site is asked again soon.
"""

import time
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

from rumo_core.sources.base import SourceError

ROBOTS_AGENT = "RumoJobSearch"
ROBOTS_TTL = 86400.0
ROBOTS_ERROR_TTL = 3600.0
ROBOTS_MAX_BYTES = 500_000


class RobotsDisallowed(SourceError):
    """robots.txt does not allow this page for our agent."""


@dataclass(frozen=True)
class RobotsRules:
    allow_all: bool
    deny_all: bool
    parser: RobotFileParser | None
    crawl_delay: float | None
    sitemaps: tuple[str, ...]

    def allowed(self, url: str) -> bool:
        if self.deny_all:
            return False
        if self.allow_all or self.parser is None:
            return True
        return self.parser.can_fetch(ROBOTS_AGENT, url)


_ALLOW_ALL = RobotsRules(True, False, None, None, ())
_DENY_ALL = RobotsRules(False, True, None, None, ())


def parse_robots(status: int | None, body: bytes | None) -> RobotsRules:
    if status in (404, 410):
        return _ALLOW_ALL
    if status != 200 or body is None:
        return _DENY_ALL
    if b"\x00" in body:
        return _DENY_ALL
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return _DENY_ALL
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    delay = parser.crawl_delay(ROBOTS_AGENT)
    try:
        crawl = float(delay) if delay is not None else None
    except (TypeError, ValueError):
        crawl = None
    sitemaps = tuple(parser.site_maps() or ())
    return RobotsRules(False, False, parser, crawl, sitemaps)


class RobotsCache:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._items: dict[str, tuple[RobotsRules, float]] = {}

    def get(self, origin: str) -> RobotsRules | None:
        item = self._items.get(origin)
        if item is None:
            return None
        rules, expires = item
        if self._clock() >= expires:
            self._items.pop(origin, None)
            return None
        return rules

    def put(self, origin: str, rules: RobotsRules, *, error: bool) -> None:
        ttl = ROBOTS_ERROR_TTL if error else ROBOTS_TTL
        self._items[origin] = (rules, self._clock() + ttl)
