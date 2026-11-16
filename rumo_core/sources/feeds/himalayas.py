"""Himalayas public jobs API (no key). Only the first page is read (no cursor).

Terms (see tests/fixtures/VERIFICATION.md): `applicationLink` is used unchanged as
the job URL, the Himalayas link is shown as the source, and listings are not
passed on to other boards. `guid` is the public job URL and serves as the id;
`pubDate` is Unix seconds. Salary is kept only when the amount, currency and
period (annual) are all present.
"""

import hashlib
from decimal import Decimal, InvalidOperation

from rumo_core.sources.ats_common import clean_str, from_epoch_s, json_of
from rumo_core.sources.base import RawJob, SalaryHint, SourceBadResponse
from rumo_core.sources.feeds.base import Attribution, FeedItem, FeedParams, policy_limits

API = "https://himalayas.app/jobs/api"
_MAX_ID = 255


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _amount(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _salary(item: dict) -> SalaryHint | None:
    currency = item.get("currency")
    if not isinstance(currency, str) or len(currency.strip()) != 3:
        return None
    if item.get("salaryPeriod") != "annual":
        return None
    low, high = _amount(item.get("minSalary")), _amount(item.get("maxSalary"))
    if low is None and high is None:
        return None
    return SalaryHint(min=low, max=high, currency=currency.strip().upper(), period="year")


def _to_item(item) -> FeedItem | None:
    if not isinstance(item, dict):
        return None
    guid = clean_str(item.get("guid"), 2000)
    title = clean_str(item.get("title"), 500)
    url = clean_str(item.get("applicationLink"), 2000)
    if guid is None or title is None or url is None:
        return None
    external_id = guid if len(guid) <= _MAX_ID else hashlib.sha256(guid.encode()).hexdigest()
    places = _strings(item.get("locationRestrictions"))
    location = f"Remote - {', '.join(places)}" if places else "Remote"
    description = item.get("description") or item.get("excerpt")
    raw = RawJob(
        external_id=external_id,
        title=title,
        url=url,
        location_text=location,
        description_html=description if isinstance(description, str) else "",
        department=None,
        posted_at=from_epoch_s(item.get("pubDate")),
        raw=item,
        work_model_hint="remote",
        salary_hint=_salary(item),
    )
    return FeedItem(raw=raw, employer_name=clean_str(item.get("companyName"), 255))


class HimalayasFeed:
    name = "himalayas"
    needs_key = False
    attribution = Attribution("Vagas via Himalayas", "https://himalayas.app")

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or 20_000_000

    def key_configured(self, settings) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def fetch(self, params: FeedParams, *, known_ids: frozenset[str] = frozenset()):
        resp = await self._client.get(
            API, kind="api", max_bytes=self._max_bytes, accept="application/json",
            **policy_limits(self.name),
        )
        data = json_of(resp, "Himalayas")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceBadResponse("formato inesperado da resposta do Himalayas")
        items = (_to_item(entry) for entry in data["jobs"])
        return [item for item in items if item is not None]
