"""Ashby public job board API (no token): https://api.ashbyhq.com/posting-api/job-board/{board}

Verified in tests/fixtures/VERIFICATION.md (Task 1): one call returns the whole
board (`jobs`, no pagination); compensation comes with includeCompensation=true.
"""

from decimal import Decimal
from urllib.parse import quote

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    from_iso,
    identifier_slug,
    json_of,
    not_found_or_raise,
    work_model,
)
from rumo_core.sources.base import RawJob, SalaryHint, SourceBadResponse

API = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
_INTERVALS = {"1 YEAR": "year", "1 MONTH": "month", "1 HOUR": "hour"}


def _salary(comp) -> SalaryHint | None:
    """First Salary component of the tiers with a known interval and a numeric amount."""
    if not isinstance(comp, dict):
        return None
    for tier in comp.get("compensationTiers") or []:
        if not isinstance(tier, dict):
            continue
        for part in tier.get("components") or []:
            if not isinstance(part, dict) or part.get("compensationType") != "Salary":
                continue
            period = _INTERVALS.get(part.get("interval"))
            currency = part.get("currencyCode")
            lo, hi = part.get("minValue"), part.get("maxValue")
            nums = [x for x in (lo, hi) if x is not None]
            if period is None or not isinstance(currency, str) or not nums:
                continue
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in nums):
                continue
            return SalaryHint(
                min=Decimal(str(lo)) if lo is not None else None,
                max=Decimal(str(hi)) if hi is not None else None,
                currency=currency.strip().upper()[:8],
                period=period,
            )
    return None


def _to_raw(item) -> RawJob | None:
    if not isinstance(item, dict) or item.get("isListed") is False:
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("title"), 500)
    url = clean_str(item.get("jobUrl"), 2000)
    if eid is None or title is None or url is None:
        return None
    description = item.get("descriptionHtml")
    hint = work_model(item.get("workplaceType"))
    if hint is None and item.get("isRemote") is True:
        hint = "remote"
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=clean_str(item.get("location"), 500) or "",
        description_html=description if isinstance(description, str) else "",
        department=clean_str(item.get("department"), 255) or clean_str(item.get("team"), 255),
        posted_at=from_iso(item.get("publishedAt")),
        raw=item,
        work_model_hint=hint,
        salary_hint=_salary(item.get("compensation")),
    )


class AshbySource:
    name = "ashby"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or value.startswith("jobs.ashbyhq") or value.startswith("api.ashbyhq"):
            from urllib.parse import urlsplit

            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if host not in ("jobs.ashbyhq.com",):
                raise ValueError("não é uma URL de vagas do Ashby")
            segs = [p for p in parts.path.split("/") if p]
            if not segs:
                raise ValueError("URL do Ashby sem a empresa")
            value = segs[0]
        return identifier_slug(value, "Ashby")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        board = self.normalize_identifier(identifier)
        resp = await self._client.get(
            API.format(board=quote(board, safe="")), kind="api",
            max_bytes=self._max_bytes, accept="application/json",
        )
        not_found_or_raise(resp, "Ashby")
        data = json_of(resp, "Ashby")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceBadResponse("formato inesperado da resposta do Ashby")
        out: dict[str, RawJob] = {}
        for item in data["jobs"]:
            raw = _to_raw(item)
            if raw is not None:
                out.pop(raw.external_id, None)
                out[raw.external_id] = raw
        return list(out.values())
