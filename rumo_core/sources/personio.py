"""Personio public XML feed (no token): https://{slug}.jobs.personio.de/xml

Verified in tests/fixtures/VERIFICATION.md (Task 3): `<workzag-jobs>` root with
`<position>` children, no paging. The XML has no URL: the public page is
https://{slug}.jobs.personio.de/job/{id} (checked by one GET in the Task 3 run
and again in the 3b implementation check: HTTP 200).

The XML is parsed only after rejecting any DOCTYPE or ENTITY declaration.
"""

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from rumo_core.config import get_settings
from rumo_core.sources.ats_common import clean_id, clean_str, from_iso, identifier_slug
from rumo_core.sources.base import RawJob, SourceBadResponse, SourceNotFound

API = "https://{slug}.jobs.personio.de/xml"
PUBLIC = "https://{slug}.jobs.personio.de/job/{id}"
_FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _text(node, tag: str) -> str | None:
    child = node.find(tag)
    return child.text if child is not None else None


def _to_raw(slug: str, node) -> RawJob | None:
    eid = clean_id(_text(node, "id"))
    title = clean_str(_text(node, "name"), 500)
    if eid is None or title is None:
        return None
    parts = []
    for desc in node.findall("jobDescriptions/jobDescription"):
        value = desc.find("value")
        if value is not None and value.text:
            parts.append(value.text)
    return RawJob(
        external_id=eid,
        title=title,
        url=PUBLIC.format(slug=slug, id=eid),
        location_text=clean_str(_text(node, "office"), 500) or "",
        description_html="".join(parts),
        department=clean_str(_text(node, "department"), 255),
        posted_at=from_iso(_text(node, "createdAt")),
        raw={"id": eid, "name": title, "office": _text(node, "office"),
             "department": _text(node, "department"), "createdAt": _text(node, "createdAt")},
    )


def parse_positions(body: bytes, slug: str) -> list[RawJob]:
    if _FORBIDDEN.search(body):
        raise SourceBadResponse("XML com DOCTYPE ou ENTITY não é aceito")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceBadResponse("XML inválido no Personio") from exc
    if root.tag != "workzag-jobs":
        raise SourceBadResponse("formato inesperado do XML do Personio")
    out: dict[str, RawJob] = {}
    for node in root.findall("position"):
        raw = _to_raw(slug, node)
        if raw is not None:
            out.pop(raw.external_id, None)
            out[raw.external_id] = raw
    return list(out.values())


class PersonioSource:
    name = "personio"

    def __init__(self, client, *, max_bytes: int | None = None) -> None:
        self._client = client
        self._max_bytes = max_bytes or get_settings().fetch_max_bytes

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if "://" in value or ".jobs.personio" in value.lower():
            parts = urlsplit(value if "://" in value else f"https://{value}")
            host = (parts.hostname or "").lower()
            if not host.endswith(".jobs.personio.de"):
                raise ValueError("não é uma URL de vagas do Personio")
            value = host[: -len(".jobs.personio.de")]
        return identifier_slug(value, "Personio")

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        slug = self.normalize_identifier(identifier)
        resp = await self._client.get(API.format(slug=slug), kind="api",
                                      max_bytes=self._max_bytes, accept="application/xml")
        if resp.status in (404, 410):
            raise SourceNotFound("Personio: quadro não encontrado")
        return parse_positions(resp.body, slug)

