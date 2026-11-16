"""Breezy HR public JSON feed (no token): https://{company}.breezy.hr/json

Verified in tests/fixtures/VERIFICATION.md (Task 1): a plain array, no paging.
The list has no description; the salary is free text, so it becomes the
description line `Salary: ...` and the normal salary rule reads it. Full
descriptions need one request per job (backlog).
"""

import html
import re
from urllib.parse import urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    from_iso,
    identifier_slug,
    json_of,
    not_found_or_raise,
)
from rumo_core.sources.base import RawJob, SourceBadResponse

API = "https://{company}.breezy.hr/json"
_SLUG = re.compile(r"[a-z0-9-]{1,63}")


def _location(loc) -> tuple[str, str | None]:
    if not isinstance(loc, dict):
        return "", None
    name = clean_str(loc.get("name"), 500)
    if name is None:
        parts = [clean_str(loc.get("city"), 120)]
        state = loc.get("state")
        if isinstance(state, dict):
            parts.append(clean_str(state.get("name"), 120))
        country = loc.get("country")
        if isinstance(country, dict):
            parts.append(clean_str(country.get("name"), 120))
        name = ", ".join(p for p in parts if p)
    hint = "remote" if loc.get("is_remote") is True else None
    return name or "", hint


def _to_raw(item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id")) or clean_id(item.get("friendly_id"))
    title = clean_str(item.get("name"), 500)
    url = clean_str(item.get("url"), 2000)
    if eid is None or title is None or url is None:
        return None
    salary = clean_str(item.get("salary"), 300)
    if salary:
        salary = re.sub(r"[<>]", "", salary)  # short free text: no markup at all
    location, hint = _location(item.get("location"))
    department = clean_str(item.get("department"), 255)
    posted = item.get("published_date")
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=location,
        description_html=f"<p>Salary: {html.escape(salary)}</p>" if salary else "",
        department=department,
        posted_at=from_iso(posted) if isinstance(posted, str) else None,
        raw=item,
        work_model_hint=hint,
    )


class BreezySource:
    name = "breezy"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or value.lower().endswith(".breezy.hr") or ".breezy.hr/" in value.lower():
            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if not host.endswith(".breezy.hr"):
                raise ValueError("não é uma URL de vagas do Breezy")
            value = host[: -len(".breezy.hr")]
        value = value.strip().lower()
        if not _SLUG.fullmatch(value):
            raise ValueError("identificador de Breezy inválido")
        return identifier_slug(value, "Breezy")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        company = self.normalize_identifier(identifier)
        resp = await self._client.get(
            API.format(company=company), kind="api", max_bytes=self._max_bytes,
            accept="application/json",
        )
        not_found_or_raise(resp, "Breezy")
        data = json_of(resp, "Breezy")
        if not isinstance(data, list):
            raise SourceBadResponse("formato inesperado da resposta do Breezy")
        out: dict[str, RawJob] = {}
        for item in data:
            raw = _to_raw(item)
            if raw is not None:
                out.pop(raw.external_id, None)
                out[raw.external_id] = raw
        return list(out.values())

