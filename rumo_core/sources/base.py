from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class SourceError(Exception):
    pass


class SourceNotFound(SourceError):
    """Board does not exist (404)."""


class SourceUnavailable(SourceError):
    """Network error, timeout, 5xx, or 429 after retries."""


class SourceBadResponse(SourceError):
    """Invalid JSON, unexpected shape, or response over the size cap."""


@dataclass(frozen=True)
class RawJob:
    external_id: str
    title: str
    url: str
    location_text: str
    description_html: str
    department: str | None
    posted_at: datetime | None
    raw: dict


class Source(Protocol):
    name: str

    async def fetch_jobs(self, identifier: str) -> list[RawJob]: ...

    def normalize_identifier(self, text: str) -> str: ...

    async def aclose(self) -> None: ...
