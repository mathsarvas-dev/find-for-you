"""BambooHR public careers list (no token): https://{slug}.bamboohr.com/careers/list

Verified in tests/fixtures/VERIFICATION.md (Task 5): JSON `{meta, result}`, no
paging. The list has no description: description_html is empty and the full
text is one request per job (backlog). Public page checked in the 3b run:
https://{slug}.bamboohr.com/careers/{id} (HTTP 200).
"""

from urllib.parse import urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    identifier_slug,
    json_of,
    not_found_or_raise,
)
from rumo_core.sources.base import RawJob, SourceBadResponse

API = "https://{slug}.bamboohr.com/careers/list"
PUBLIC = "https://{slug}.bamboohr.com/careers/{id}"


def _location(item) -> str:
    loc = item.get("atsLocation") if isinstance(item.get("atsLocation"), dict) else {}
    if not loc.get("city") and not loc.get("country"):
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
    parts = [clean_str(loc.get(k), 120) for k in ("city", "state", "province", "country")]
    return ", ".join(p for p in parts if p)


def _to_raw(slug: str, item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("jobOpeningName"), 500)
    if eid is None or title is None:
        return None
    return RawJob(
        external_id=eid,
        title=title,
        url=PUBLIC.format(slug=slug, id=eid),
        location_text=_location(item),
        description_html="",
        department=clean_str(item.get("departmentLabel"), 255),
        posted_at=None,
        raw=item,
        work_model_hint="remote" if item.get("isRemote") is True else None,
    )


class BambooHRSource:
    name = "bamboohr"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or ".bamboohr.com" in value.lower():
            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if not host.endswith(".bamboohr.com"):
                raise ValueError("não é uma URL de vagas do BambooHR")
            value = host[: -len(".bamboohr.com")]
        return identifier_slug(value, "BambooHR")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        slug = self.normalize_identifier(identifier)
        resp = await self._client.get(API.format(slug=slug), kind="api",
                                      max_bytes=self._max_bytes, accept="application/json")
        not_found_or_raise(resp, "BambooHR")
        data = json_of(resp, "BambooHR")
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise SourceBadResponse("formato inesperado da resposta do BambooHR")
        out: dict[str, RawJob] = {}
        for item in data["result"]:
            raw = _to_raw(slug, item)
            if raw is not None:
                out.pop(raw.external_id, None)
                out[raw.external_id] = raw
        return list(out.values())
