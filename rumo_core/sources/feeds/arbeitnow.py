"""Arbeitnow public job board API (no key). Only the first page is read.

Terms (see tests/fixtures/VERIFICATION.md): link back to the site and credit it
(`meta.terms`). `created_at` is Unix seconds. `remote: true` gives the remote hint.
"""

from rumo_core.sources.ats_common import clean_str, from_epoch_s, json_of
from rumo_core.sources.base import RawJob, SourceBadResponse
from rumo_core.sources.feeds.base import Attribution, FeedItem, FeedParams, policy_limits

API = "https://www.arbeitnow.com/api/job-board-api"


def _to_item(item) -> FeedItem | None:
    if not isinstance(item, dict):
        return None
    eid = clean_str(item.get("slug"), 255)
    title = clean_str(item.get("title"), 500)
    url = clean_str(item.get("url"), 2000)
    if eid is None or title is None or url is None:
        return None
    description = item.get("description")
    raw = RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=clean_str(item.get("location"), 500) or "",
        description_html=description if isinstance(description, str) else "",
        department=None,
        posted_at=from_epoch_s(item.get("created_at")),
        raw=item,
        work_model_hint="remote" if item.get("remote") is True else None,
    )
    return FeedItem(raw=raw, employer_name=clean_str(item.get("company_name"), 255))


class ArbeitnowFeed:
    name = "arbeitnow"
    needs_key = False
    attribution = Attribution("Vagas via Arbeitnow", "https://www.arbeitnow.com")

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
        data = json_of(resp, "Arbeitnow")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise SourceBadResponse("formato inesperado da resposta do Arbeitnow")
        items = (_to_item(entry) for entry in data["data"])
        return [item for item in items if item is not None]
