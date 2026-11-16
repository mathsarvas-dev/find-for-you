"""Teamtailor public RSS feed (no token): https://{slug}.teamtailor.com/jobs.rss

Verified in tests/fixtures/VERIFICATION.md (Task 4): RSS 2.0 with the `tt`
namespace (locations, department, role). No paging. The description is HTML
escaped inside the XML; parsing it gives the HTML as text, which normalize
then treats as data. DOCTYPE/ENTITY declarations are refused (as in Personio).
"""

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import clean_str, identifier_slug, not_found_or_raise, work_model
from rumo_core.sources.base import RawJob, SourceBadResponse
from rumo_core.sources.personio import _FORBIDDEN

API = "https://{slug}.teamtailor.com/jobs.rss"
TT = "{https://teamtailor.com/locations}"


def _locations(item) -> str:
    names: list[str] = []
    for loc in item.findall(f"{TT}locations/{TT}location"):
        city = clean_str(loc.findtext(f"{TT}city"), 120)
        country = clean_str(loc.findtext(f"{TT}country"), 120)
        text = ", ".join(p for p in (city, country) if p)
        if text:
            names.append(text)
    return "; ".join(names)


def _posted(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _to_raw(slug: str, item) -> RawJob | None:
    title = clean_str(item.findtext("title"), 500)
    url = clean_str(item.findtext("link"), 2000)
    guid = clean_str(item.findtext("guid"), 255)
    eid = guid or url
    if title is None or url is None or eid is None:
        return None
    return RawJob(
        external_id=eid,
        title=title,
        url=url,
        location_text=_locations(item),
        description_html=item.findtext("description") or "",
        department=clean_str(item.findtext(f"{TT}department"), 255),
        posted_at=_posted((item.findtext("pubDate") or "").strip()),
        raw={"guid": guid, "title": title, "link": url},
        work_model_hint=work_model(item.findtext("remoteStatus")),
    )


def parse_items(body: bytes, slug: str) -> list[RawJob]:
    if _FORBIDDEN.search(body):
        raise SourceBadResponse("RSS com DOCTYPE ou ENTITY não é aceito")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceBadResponse("RSS inválido no Teamtailor") from exc
    if root.tag != "rss":
        raise SourceBadResponse("formato inesperado do RSS do Teamtailor")
    out: dict[str, RawJob] = {}
    for item in root.findall("channel/item"):
        raw = _to_raw(slug, item)
        if raw is not None:
            out.pop(raw.external_id, None)
            out[raw.external_id] = raw
    return list(out.values())


class TeamtailorSource:
    name = "teamtailor"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or ".teamtailor.com" in value.lower():
            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if not host.endswith(".teamtailor.com"):
                raise ValueError("não é uma URL de vagas do Teamtailor")
            value = host[: -len(".teamtailor.com")]
        return identifier_slug(value, "Teamtailor")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        slug = self.normalize_identifier(identifier)
        resp = await self._client.get(API.format(slug=slug), kind="api",
                                      max_bytes=self._max_bytes, accept="application/rss+xml")
        not_found_or_raise(resp, "Teamtailor")
        return parse_items(resp.body, slug)
