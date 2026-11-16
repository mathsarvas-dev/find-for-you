"""Validate a discovery candidate by fetching its board through the connector."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from rumo_core.sources.base import (
    RawJob,
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

    kind: valid (>= 1 accepted open job), invalid (board missing, no open jobs,
    or none of them accepted by the user's hard filters) or transient (try
    again later). error: exception class name only, never a message (it may
    echo a response body); None for a clean answer. resolved: (source,
    identifier) of the company to create when the candidate was a career page
    that embeds an ATS or has job postings of its own; None = the candidate's own.
    """

    kind: Literal["valid", "invalid", "transient"]
    open_jobs: int
    name: str | None
    error: str | None = None
    resolved: tuple[str, str] | None = None


def _company_name(raws) -> str | None:
    for r in raws:
        value = r.raw.get("company_name") if isinstance(r.raw, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_NAME_LEN]
    return None


# paths tried on a candidate domain, in addition to its home page
CAREER_PATHS = ("/careers", "/jobs", "/carreiras", "/vagas", "/trabalhe-conosco")
_MAX_IDENTIFIER = 255


async def validate_site_candidate(
    site, domain: str, *, accept: Callable[[RawJob], bool] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS, source_getter=None,
) -> Outcome:
    """Find the job postings of an employer's website (`site` candidate).

    Probe the home page and CAREER_PATHS: an embedded ATS board is validated
    with its own connector and the company is created for that board
    (`resolved`); job postings in the page (or on its job pages) make a `site`
    company. A portal domain is never probed (the gate forbids it).
    """
    from rumo_core.sources import get_source
    from rumo_core.sources.base import SourceBlocked
    from rumo_core.sources.portals.gate import portal_for_url

    if portal_for_url(f"https://{domain}/") is not None:
        return Outcome("invalid", 0, None)
    try:
        probe = await asyncio.wait_for(
            site.probe(f"https://{domain}/", extra_paths=CAREER_PATHS), timeout=timeout,
        )
    except (SourceBlocked, SourceUnavailable, TimeoutError) as exc:
        return Outcome("transient", 0, None, type(exc).__name__)
    except (SourceBadResponse, ValueError) as exc:  # unsafe or malformed address
        return Outcome("invalid", 0, None, type(exc).__name__)
    if probe.kind == "ats" and probe.source and probe.identifier:
        try:
            ats = (source_getter or get_source)(probe.source)
        except KeyError:
            return Outcome("invalid", 0, None)
        outcome = await validate_candidate(ats, probe.identifier, accept=accept, timeout=timeout)
        if outcome.kind == "valid":
            return replace(outcome, resolved=(probe.source, probe.identifier))
        return outcome
    if probe.kind == "jsonld" and probe.identifier and len(probe.identifier) <= _MAX_IDENTIFIER:
        accepted = [r for r in probe.raws if accept is None or accept(r)]
        if not accepted:
            return Outcome("invalid", 0, None)
        return Outcome(
            "valid", len(accepted), _company_name(accepted),
            resolved=("site", probe.identifier),
        )
    return Outcome("invalid", 0, None)


async def validate_candidate(
    source: Source, token: str, *, accept: Callable[[RawJob], bool] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Outcome:
    """Fetch the board once and classify it. Never raises for expected
    connector failures; uses only the given source (no other host, no LLM).

    `accept` (the user's hard filters, see the funnel) decides which open jobs
    count: a board whose jobs are all refused is `invalid`, as an empty one.
    """
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
    accepted = [r for r in raws if accept is None or accept(r)]
    if not accepted:
        return Outcome("invalid", 0, _company_name(raws))
    return Outcome("valid", len(accepted), _company_name(accepted))
