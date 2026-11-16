"""Remote OK public API (no key). The first array element is a legal notice, not a job.

Terms (see tests/fixtures/VERIFICATION.md): link back to the job's Remote OK URL
and credit the source. The salary fields are not mapped: their currency is not
verified. One request per run.
"""

from rumo_core.sources.ats_common import clean_id, clean_str, from_epoch_s, from_iso, json_of
from rumo_core.sources.base import RawJob, SourceBadResponse
from rumo_core.sources.feeds.base import Attribution, FeedItem, FeedParams, policy_limits

API = "https://remoteok.com/api"


def _to_item(item) -> FeedItem | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("position"), 500)
    url = clean_str(item.get("url"), 2000)
    if eid is None or title is None or url is None:
        return None  # the legal notice and any malformed element
    description = item.get("description")
    raw = RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=clean_str(item.get("location"), 500) or "",
        description_html=description if isinstance(description, str) else "",
        department=None,
        posted_at=from_iso(item.get("date")) or from_epoch_s(item.get("epoch")),
        raw=item,
        work_model_hint="remote",
    )
    return FeedItem(raw=raw, employer_name=clean_str(item.get("company"), 255))


class RemoteOKFeed:
    name = "remoteok"
    needs_key = False
    attribution = Attribution("Vagas via Remote OK", "https://remoteok.com")

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
        data = json_of(resp, "Remote OK")
        if not isinstance(data, list):
            raise SourceBadResponse("formato inesperado da resposta do Remote OK")
        items = (_to_item(entry) for entry in data)
        return [item for item in items if item is not None]
