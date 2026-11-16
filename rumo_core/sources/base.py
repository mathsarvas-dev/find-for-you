from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class SourceError(Exception):
    pass


class SourceNotFound(SourceError):
    """Board does not exist (404)."""


class SourceUnavailable(SourceError):
    """Network error, timeout, 5xx, or 429 after retries."""


class SourceBadResponse(SourceError):
    """Invalid JSON, unexpected shape, or response over the size cap."""


class SourceBlocked(SourceError):
    """The site refused access repeatedly (403/429): the host is suspended."""

    def __init__(self, host: str) -> None:
        super().__init__("o site recusou o acesso de forma repetida")
        self.host = host


@dataclass(frozen=True)
class SalaryHint:
    min: Decimal | None
    max: Decimal | None
    currency: str
    period: str | None


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
    # optional structured hints from the source (marco 3b); None = use the text rules
    work_model_hint: str | None = None
    country_hint: str | None = None
    salary_hint: SalaryHint | None = None


# Connectors whose jobs come from an applicant tracking system (rank 0).
ATS_SOURCES: frozenset[str] = frozenset(
    {"greenhouse", "lever", "ashby", "smartrecruiters", "breezy"}
)


def source_rank_for(source: str) -> int:
    """ATS = 0; career pages and pasted links = 1; aggregators/portals = 2."""
    if source in ATS_SOURCES:
        return 0
    if source in ("site", "link"):
        return 1
    return 2


class Source(Protocol):
    name: str

    async def fetch_jobs(self, identifier: str) -> list[RawJob]: ...

    def normalize_identifier(self, text: str) -> str: ...

    async def aclose(self) -> None: ...
