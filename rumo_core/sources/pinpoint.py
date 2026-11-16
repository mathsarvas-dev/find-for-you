"""Pinpoint public postings (no token): https://{slug}.pinpointhq.com/postings.json

Verified in tests/fixtures/VERIFICATION.md (Task 7): `{data: [...]}` with the
public URL and the description inline. Salary is not mapped: the verified
fixture has no compensation values to base a mapping on.
"""

from urllib.parse import urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import (
    clean_id,
    clean_str,
    identifier_slug,
    json_of,
    not_found_or_raise,
    work_model,
)
from rumo_core.sources.base import RawJob, SourceBadResponse

API = "https://{slug}.pinpointhq.com/postings.json"
_DESC_FIELDS = ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")


def _to_raw(item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    eid = clean_id(item.get("id"))
    title = clean_str(item.get("title"), 500)
    url = clean_str(item.get("url"), 2000)
    if eid is None or title is None or url is None:
        return None
    loc = item.get("location") if isinstance(item.get("location"), dict) else {}
    location = ", ".join(p for p in (clean_str(loc.get(k), 120) for k in ("city", "province", "name")) if p)
    job = item.get("job") if isinstance(item.get("job"), dict) else {}
    dept = job.get("department") if isinstance(job.get("department"), dict) else {}
    description = "".join(v for v in (item.get(k) for k in _DESC_FIELDS) if isinstance(v, str))
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=location,
        description_html=description,
        department=clean_str(dept.get("name"), 255),
        posted_at=None,
        raw=item,
        work_model_hint=work_model(item.get("workplace_type")),
    )


class PinpointSource:
    name = "pinpoint"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or ".pinpointhq.com" in value.lower():
            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if not host.endswith(".pinpointhq.com"):
                raise ValueError("não é uma URL de vagas do Pinpoint")
            value = host[: -len(".pinpointhq.com")]
        return identifier_slug(value, "Pinpoint")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        slug = self.normalize_identifier(identifier)
        resp = await self._client.get(API.format(slug=slug), kind="api",
                                      max_bytes=self._max_bytes, accept="application/json")
        not_found_or_raise(resp, "Pinpoint")
        data = json_of(resp, "Pinpoint")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise SourceBadResponse("formato inesperado da resposta do Pinpoint")
        out: dict[str, RawJob] = {}
        for item in data["data"]:
            raw = _to_raw(item)
            if raw is not None:
                out.pop(raw.external_id, None)
                out[raw.external_id] = raw
        return list(out.values())
