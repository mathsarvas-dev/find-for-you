"""Lever postings API (public, no token): https://api.lever.co/v0/postings/{site}

Verified in tests/fixtures/VERIFICATION.md (Task 1). Paged with skip/limit;
a short page ends the list. A list that never ends (max_pages) is refused
instead of partly ingested, so open jobs are not closed by a truncated read.
"""

import html
from urllib.parse import quote

from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    country_code,
    from_epoch_ms,
    identifier_slug,
    json_of,
    not_found_or_raise,
    work_model,
)
from rumo_core.sources.base import RawJob, SalaryHint, SourceBadResponse
from decimal import Decimal

API = "https://api.lever.co/v0/postings/{site}?mode=json&skip={skip}&limit={limit}"
API_EU = "https://api.eu.lever.co/v0/postings/{site}?mode=json&skip={skip}&limit={limit}"
_HOSTS = ("jobs.lever.co", "jobs.eu.lever.co")
_INTERVALS = {"per-year-salary": "year", "per-month-salary": "month", "per-hour-salary": "hour"}


def _salary(value) -> SalaryHint | None:
    if not isinstance(value, dict):
        return None
    interval = _INTERVALS.get(value.get("interval"))
    currency = value.get("currency")
    lo, hi = value.get("min"), value.get("max")
    if interval is None or not isinstance(currency, str) or not currency.strip():
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (lo, hi) if x is not None):
        return None
    if lo is None and hi is None:
        return None
    return SalaryHint(
        min=Decimal(str(lo)) if lo is not None else None,
        max=Decimal(str(hi)) if hi is not None else None,
        currency=currency.strip().upper()[:8],
        period=interval,
    )


def _description(item: dict) -> str:
    parts: list[str] = []
    for key in ("description", "descriptionBody"):
        if isinstance(item.get(key), str) and item[key]:
            parts.append(item[key])
            break
    lists = item.get("lists")
    if isinstance(lists, list):
        for block in lists:
            if not isinstance(block, dict):
                continue
            title = block.get("text")
            content = block.get("content")
            if isinstance(title, str) and isinstance(content, str):
                parts.append(f"<h3>{html.escape(title)}</h3><ul>{content}</ul>")
    for key in ("additional",):
        if isinstance(item.get(key), str) and item[key]:
            parts.append(item[key])
    return "".join(parts)


def _to_raw(item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("text"), 500)
    url = clean_str(item.get("hostedUrl"), 2000)
    if eid is None or title is None or url is None:
        return None
    cats = item.get("categories") if isinstance(item.get("categories"), dict) else {}
    location = clean_str(cats.get("location"), 500) or ""
    department = clean_str(cats.get("department"), 255) or clean_str(cats.get("team"), 255)
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=location,
        description_html=_description(item),
        department=department,
        posted_at=from_epoch_ms(item.get("createdAt")),
        raw=item,
        work_model_hint=work_model(item.get("workplaceType")),
        country_hint=country_code(item.get("country")),
        salary_hint=_salary(item.get("salaryRange")),
    )


class LeverSource:
    name = "lever"
    page_size = 100
    max_pages = 20

    def __init__(self, client, *, max_bytes: int) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or value.startswith("jobs."):
            from urllib.parse import urlsplit

            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if host not in _HOSTS:
                raise ValueError("não é uma URL de vagas do Lever")
            segs = [p for p in parts.path.split("/") if p]
            if not segs:
                raise ValueError("URL do Lever sem a empresa")
            return identifier_slug(segs[0], "Lever")
        if value.lower().startswith("eu:"):
            return "eu:" + identifier_slug(value[3:], "Lever")
        return identifier_slug(value, "Lever")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        site = self.normalize_identifier(identifier)
        template = API
        if site.startswith("eu:"):
            site = site[3:]
            template = API_EU
        out: dict[str, RawJob] = {}
        for page in range(self.max_pages):
            skip = page * self.page_size
            url = template.format(site=quote(site, safe=""), skip=skip, limit=self.page_size)
            resp = await self._client.get(url, kind="api", max_bytes=self._max_bytes,
                                          accept="application/json")
            not_found_or_raise(resp, "Lever")
            data = json_of(resp, "Lever")
            if not isinstance(data, list):
                raise SourceBadResponse("formato inesperado da resposta do Lever")
            for item in data:
                raw = _to_raw(item)
                if raw is not None:
                    out.pop(raw.external_id, None)
                    out[raw.external_id] = raw  # last occurrence wins
            if len(data) < self.page_size:
                return list(out.values())
        raise SourceBadResponse("lista do Lever longa demais; nada foi gravado")
