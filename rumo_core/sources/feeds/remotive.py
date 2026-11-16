"""Remotive public remote-jobs API (no key). One request per run, no paging.

Remotive's terms (see tests/fixtures/VERIFICATION.md): at most 4 calls a day,
listings may be 24 h late, the source must be credited and linked. The policy
interval and daily cap in `policy.json` are passed to `SafeClient`. The API's
own filters are not verified, so keywords and country are applied later by
the funnel, not by the request.
"""

import html

from rumo_core.sources.ats_common import clean_id, clean_str, from_iso, json_of
from rumo_core.sources.base import RawJob, SourceBadResponse
from rumo_core.sources.feeds.base import Attribution, FeedItem, FeedParams, policy_limits

API = "https://remotive.com/api/remote-jobs"


def _to_item(item) -> FeedItem | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("title"), 500)
    url = clean_str(item.get("url"), 2000)
    if eid is None or title is None or url is None:
        return None
    description = item.get("description")
    description = description if isinstance(description, str) else ""
    salary = clean_str(item.get("salary"), 300)
    if salary:
        description = f"<p>Salary: {html.escape(salary)}</p>" + description
    raw = RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=clean_str(item.get("candidate_required_location"), 500) or "",
        description_html=description,
        department=clean_str(item.get("category"), 255),
        posted_at=from_iso(item.get("publication_date")),
        raw=item,
        work_model_hint="remote",
    )
    return FeedItem(raw=raw, employer_name=clean_str(item.get("company_name"), 255))


class RemotiveFeed:
    name = "remotive"
    needs_key = False
    attribution = Attribution("Vagas via Remotive", "https://remotive.com")

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or 20_000_000

    def key_configured(self, settings) -> bool:
        return True

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    async def fetch(self, params: FeedParams, *, known_ids: frozenset[str] = frozenset()):
        resp = await self._client.get(
            API, kind="api", max_bytes=self._max_bytes, accept="application/json",
            **policy_limits(self.name),
        )
        data = json_of(resp, "Remotive")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceBadResponse("formato inesperado da resposta do Remotive")
        items = (_to_item(entry) for entry in data["jobs"])
        return [item for item in items if item is not None]
