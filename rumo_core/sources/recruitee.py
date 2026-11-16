"""Recruitee public offers API (no token): https://{slug}.recruitee.com/api/offers/

Verified in tests/fixtures/VERIFICATION.md (Task 2): one call, `offers` array,
no paging. `careers_url` is the public page of each offer.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    country_code,
    identifier_slug,
    json_of,
    not_found_or_raise,
)
from rumo_core.sources.base import RawJob, SalaryHint, SourceBadResponse

API = "https://{slug}.recruitee.com/api/offers/"
_PERIODS = {"year": "year", "month": "month", "hour": "hour"}


def _dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _salary(value) -> SalaryHint | None:
    if not isinstance(value, dict):
        return None
    period = _PERIODS.get(value.get("period"))
    currency = clean_str(value.get("currency"), 8)
    lo, hi = _dec(value.get("min")), _dec(value.get("max"))
    if period is None or currency is None or (lo is None and hi is None):
        return None
    return SalaryHint(min=lo, max=hi, currency=currency.upper(), period=period)


def _posted(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    except ValueError:
        return None


def _work(item: dict) -> str | None:
    if item.get("remote") is True:
        return "remote"
    if item.get("hybrid") is True:
        return "hybrid"
    if item.get("on_site") is True:
        return "onsite"
    return None


def _to_raw(item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("title"), 500)
    url = clean_str(item.get("careers_url"), 2000)
    if eid is None or title is None or url is None:
        return None
    parts = [x for x in (item.get("description"), item.get("requirements")) if isinstance(x, str)]
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=clean_str(item.get("location"), 500) or "",
        description_html="".join(parts),
        department=clean_str(item.get("department"), 255),
        posted_at=_posted(item.get("published_at")),
        raw=item,
        work_model_hint=_work(item),
        country_hint=country_code((item.get("country_code") or "").upper()),
        salary_hint=_salary(item.get("salary")),
    )


class RecruiteeSource:
    name = "recruitee"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or ".recruitee.com" in value.lower():
            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if not host.endswith(".recruitee.com"):
                raise ValueError("não é uma URL de vagas do Recruitee")
            value = host[: -len(".recruitee.com")]
        if not re.fullmatch(r"[a-z0-9-]{1,63}", value.strip().lower()):
            raise ValueError("identificador de Recruitee inválido")
        return identifier_slug(value, "Recruitee")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        slug = self.normalize_identifier(identifier)
        resp = await self._client.get(API.format(slug=slug), kind="api",
                                      max_bytes=self._max_bytes, accept="application/json")
        not_found_or_raise(resp, "Recruitee")
        data = json_of(resp, "Recruitee")
        if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
            raise SourceBadResponse("formato inesperado da resposta do Recruitee")
        out: dict[str, RawJob] = {}
        for item in data["offers"]:
            raw = _to_raw(item)
            if raw is not None:
                out.pop(raw.external_id, None)
                out[raw.external_id] = raw
        return list(out.values())
