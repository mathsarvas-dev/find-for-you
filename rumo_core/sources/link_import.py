"""Import one job the person pasted a link to (manual choice, outside the funnel).

`read_link` reads the page's JobPosting JSON-LD through `SafeClient`, so robots
and the address checks still apply; when that is not possible the person pastes
the title and description, which are stored as escaped text (never as their
HTML). The job goes to a synthetic company per employer (`source="link"`,
inactive, `origin="link"`, rank 1). Importing never closes other jobs.
"""

import hashlib
import html as html_lib
from dataclasses import replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from rumo_core.discovery.names import employer_key
from rumo_core.models import Company, Job
from rumo_core.sources.base import RawJob, SourceBadResponse, SourceBlocked, SourceUnavailable
from rumo_core.sources.http import SiteRefused, SafeClient, UnsafeURL, canonical_url
from rumo_core.sources.ingest import IngestPolicy, ingest_jobs
from rumo_core.sources.jsonld import employer_name, extract_job_postings
from rumo_core.sources.robots import RobotsDisallowed

MAX_TITLE = 300
MAX_DESCRIPTION = 50_000
LINK_COMPANY_FALLBACK = "_sem_empresa"
LINK_COMPANY_NAME = "Vagas importadas por link"


class NeedsPastedText(Exception):
    """The page cannot be read automatically; the person must paste the text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # no_structured_data | robots | refused | unreachable


def _url_key(url: str) -> str:
    """Stable external id for a link-imported job: hash of its canonical URL."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


async def read_link(client: SafeClient, url: str) -> RawJob:
    try:
        page = await client.get(url, kind="page")
    except UnsafeURL:
        raise
    except RobotsDisallowed:
        raise NeedsPastedText("robots") from None
    except (SourceBlocked, SiteRefused):
        raise NeedsPastedText("refused") from None
    except (SourceUnavailable, SourceBadResponse):
        raise NeedsPastedText("unreachable") from None
    raws = extract_job_postings(page.text(), base_url=page.url)
    if not raws:
        raise NeedsPastedText("no_structured_data")
    return replace(raws[0], external_id=_url_key(url))


def pasted_raw(url: str, title: str, description: str) -> RawJob:
    """A job from the person's own pasted title and description (escaped, never HTML)."""
    canonical = canonical_url(url)
    paragraphs = "".join(
        f"<p>{html_lib.escape(line.strip())}</p>"
        for line in description[:MAX_DESCRIPTION].splitlines() if line.strip()
    )
    return RawJob(
        external_id=_url_key(canonical),
        title=title.strip()[:MAX_TITLE],
        url=canonical,
        location_text="",
        description_html=paragraphs,
        department=None,
        posted_at=None,
        raw={"origin": "pasted"},
    )


def _link_company(session: Session, user_id: int, raw: RawJob) -> Company:
    employer = employer_name(raw)
    name = employer or LINK_COMPANY_NAME
    identifier = employer_key(employer) or LINK_COMPANY_FALLBACK
    company = session.scalar(
        select(Company).where(
            Company.user_id == user_id, Company.source == "link",
            Company.identifier == identifier,
        )
    )
    if company is None:
        company = Company(
            user_id=user_id, source="link", identifier=identifier, name=name[:255],
            origin="link", active=False,
        )
        session.add(company)
        session.flush()
    return company


def save_imported_job(
    session: Session, user_id: int, url: str, raw: RawJob, *, now: datetime,
) -> tuple[Job, bool]:
    """Store (or refresh) the imported job; returns (job, created). Does not commit."""
    company = _link_company(session, user_id, raw)
    existed = session.scalar(
        select(Job.id).where(Job.company_id == company.id, Job.external_id == raw.external_id)
    ) is not None
    ingest_jobs(
        session, user_id, company, [raw], now=now,
        policy=IngestPolicy(origin="link", source_rank=1, close_missing=False),
    )
    job = session.scalar(
        select(Job).where(Job.company_id == company.id, Job.external_id == raw.external_id)
    )
    return job, not existed
