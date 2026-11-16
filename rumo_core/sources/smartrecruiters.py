"""SmartRecruiters public postings API (no token): /v1/companies/{id}/postings

Verified in tests/fixtures/VERIFICATION.md (Task 1): offset/limit paging with
`totalFound`. The list has NO description (the detail is one request per job,
N+1, backlog): description_html is empty by design. Public URL pattern (checked
against the detail's postingUrl): jobs.smartrecruiters.com/{company}/{id}-{slug}.
"""

import re
from urllib.parse import quote, urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    country_code,
    from_iso,
    identifier_slug,
    json_of,
    not_found_or_raise,
)
from rumo_core.sources.base import RawJob, SourceBadResponse

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings?limit={limit}&offset={offset}"
PUBLIC = "https://jobs.smartrecruiters.com/{company}/{id}-{slug}"
_HOSTS = ("jobs.smartrecruiters.com", "careers.smartrecruiters.com")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:120]


def _location(loc) -> tuple[str, str | None, str | None]:
    if not isinstance(loc, dict):
        return "", None, None
    full = clean_str(loc.get("fullLocation"), 500)
    if full is None:
        parts = [clean_str(loc.get(k), 120) for k in ("city", "region")]
        full = ", ".join(p for p in parts if p)
    work = "remote" if loc.get("remote") is True else ("hybrid" if loc.get("hybrid") is True else None)
    country = country_code(loc.get("country").upper()) if isinstance(loc.get("country"), str) else None
    return full or "", work, country


def _to_raw(company: str, item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("name"), 500)
    if eid is None or title is None:
        return None
    url = PUBLIC.format(company=quote(company, safe=""), id=quote(eid, safe=""), slug=_slug(title))
    location, work, country = _location(item.get("location"))
    dept = item.get("department")
    department = clean_str(dept.get("label"), 255) if isinstance(dept, dict) else None
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=location,
        description_html="",
        department=department,
        posted_at=from_iso(item.get("releasedDate")),
        raw=item,
        work_model_hint=work,
        country_hint=country,
    )


class SmartRecruitersSource:
    name = "smartrecruiters"
    limit = 100
    max_pages = 20

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or value.startswith("jobs.smartrecruiters") or value.startswith("careers.smartrecruiters"):
            parts = urlsplit(value if "://" in value else f"https://{value}")
            if (parts.hostname or "").lower() not in _HOSTS:
                raise ValueError("não é uma URL de vagas do SmartRecruiters")
            segs = [p for p in parts.path.split("/") if p]
            if not segs:
                raise ValueError("URL do SmartRecruiters sem a empresa")
            value = segs[0]
        return identifier_slug(value, "SmartRecruiters")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        company = self.normalize_identifier(identifier)
        out: dict[str, RawJob] = {}
        offset = 0
        for _ in range(self.max_pages):
            url = API.format(company=quote(company, safe=""), limit=self.limit, offset=offset)
            resp = await self._client.get(url, kind="api", max_bytes=self._max_bytes,
                                          accept="application/json")
            not_found_or_raise(resp, "SmartRecruiters")
            data = json_of(resp, "SmartRecruiters")
            if not isinstance(data, dict) or not isinstance(data.get("content"), list):
                raise SourceBadResponse("formato inesperado da resposta do SmartRecruiters")
            content = data["content"]
            for item in content:
                raw = _to_raw(company, item)
                if raw is not None:
                    out.pop(raw.external_id, None)
                    out[raw.external_id] = raw
            offset += len(content)
            total = data.get("totalFound")
            if not content or len(content) < self.limit or (isinstance(total, int) and offset >= total):
                return list(out.values())
        raise SourceBadResponse("lista do SmartRecruiters longa demais; nada foi gravado")
