"""Career pages (`site` source): JSON-LD postings, embedded ATS boards, job links, sitemaps.

Order for one page: (1) JobPosting JSON-LD on the page itself; (2) an ATS board
embedded in the page (the caller then connects that board's API instead);
(3) same-domain links that look like job pages, else the sitemap entries under
the page's path. Every fetch goes through `SafeClient` (robots, limits, SSRF).
A job page that fails alone is skipped; a blocked host always propagates.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from rumo_core.sources.base import RawJob, SourceBadResponse, SourceError, SourceUnavailable
from rumo_core.sources.http import SafeClient, UnsafeURL, canonical_url, registrable_domain
from rumo_core.sources.jsonld import extract_job_postings
from rumo_core.sources.robots import RobotsDisallowed

MSG_NO_STRUCTURED = (
    "Esta página não tem dados estruturados de vagas. "
    "Use “Importar por link” para vagas específicas."
)
JOB_WORDS = (
    "job", "jobs", "vaga", "vagas", "career", "careers", "carreira",
    "position", "opening", "stellen", "oportunidade",
)
MAX_SITEMAP_FILES = 5
SITEMAP_MAX_BYTES = 20_000_000
MAX_PROBE_EXTRA = 5
PROBE_JOB_PAGES = 5
_EMBED_ATTRS = {"iframe": ("src", "data-src"), "script": ("src", "data-src"),
                "link": ("href",), "a": ("href",)}
_SKIPPABLE = (UnsafeURL, RobotsDisallowed, SourceBadResponse, SourceUnavailable)


class EmbeddedAtsFound(SourceError):
    """The career page embeds a supported ATS board: connect that board instead."""

    def __init__(self, source: str, identifier: str) -> None:
        super().__init__("a página embute um quadro de vagas conhecido")
        self.source = source
        self.identifier = identifier


class NoStructuredData(SourceError):
    """No job postings could be read from the page or its links."""


@dataclass(frozen=True)
class SiteFetch:
    raws: list[RawJob]
    listed_ids: frozenset[str]


@dataclass(frozen=True)
class ProbeResult:
    kind: str  # "ats" | "jsonld" | "none"
    source: str | None
    identifier: str | None
    raws: list[RawJob]


class _Scan(HTMLParser):
    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        self._names = names
        self.found: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._names:
            self.found.append((tag, {(k or "").lower(): (v or "") for k, v in attrs}))


def _scan(html: str, names: tuple[str, ...]) -> list[tuple[str, dict[str, str]]]:
    parser = _Scan(names)
    parser.feed(html or "")
    parser.close()
    return parser.found


def detect_embedded_ats(html: str, page_url: str) -> tuple[str, str] | None:
    """(source, identifier) of the first supported ATS URL in the page's embeds and links."""
    from rumo_core.sources import ats_from_url  # local: the package imports this module

    for tag, attrs in _scan(html, tuple(_EMBED_ATTRS)):
        for attr in _EMBED_ATTRS[tag]:
            value = attrs.get(attr, "").strip()
            if not value:
                continue
            hit = ats_from_url(urljoin(page_url, value))
            if hit is not None:
                return hit
    return None


def job_links(html: str, page_url: str, *, limit: int) -> list[str]:
    """Same-domain links whose path looks like a job page, in page order, unique."""
    try:
        page = canonical_url(page_url)
    except UnsafeURL:
        return []
    domain = registrable_domain(urlsplit(page).hostname or "")
    out: list[str] = []
    seen: set[str] = set()
    for _tag, attrs in _scan(html, ("a",)):
        href = attrs.get("href", "").strip()
        if not href:
            continue
        try:
            link = canonical_url(urljoin(page, href))
        except UnsafeURL:
            continue
        if link == page or link in seen:
            continue
        parts = urlsplit(link)
        if registrable_domain(parts.hostname or "") != domain:
            continue
        path = parts.path.lower()
        if not any(word in path for word in JOB_WORDS):
            continue
        seen.add(link)
        out.append(link)
        if len(out) >= limit:
            break
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_sitemap(body: bytes) -> tuple[str, list[str]]:
    lowered = body.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise SourceBadResponse("sitemap com declaração DTD não é aceito")
    try:
        root = ET.fromstring(body)  # noqa: S314 - DTDs are rejected above, so no entities
    except ET.ParseError as exc:
        raise SourceBadResponse("sitemap inválido") from exc
    kind = _local(root.tag)
    if kind not in ("sitemapindex", "urlset"):
        raise SourceBadResponse("formato de sitemap inesperado")
    child = "sitemap" if kind == "sitemapindex" else "url"
    locs: list[str] = []
    for entry in root:
        if _local(entry.tag) != child:
            continue
        for sub in entry:
            if _local(sub.tag) == "loc" and sub.text and sub.text.strip():
                locs.append(sub.text.strip())
    return kind, locs


async def sitemap_links(client: SafeClient, page_url: str, *, limit: int) -> list[str]:
    """URLs under the career page's path listed in its sitemaps (index followed one level)."""
    page = canonical_url(page_url)
    parts = urlsplit(page)
    origin = f"{parts.scheme}://{parts.netloc}"
    domain = registrable_domain(parts.hostname or "")
    prefix = parts.path.rstrip("/")
    listed = await client.sitemaps_for(origin)
    pending = [(url, 0) for url in (listed or (origin + "/sitemap.xml",))]
    files = 0
    out: list[str] = []
    seen: set[str] = set()
    while pending and files < MAX_SITEMAP_FILES and len(out) < limit:
        url, depth = pending.pop(0)
        files += 1
        resp = await client.get(url, kind="page", max_bytes=SITEMAP_MAX_BYTES)
        kind, locs = _parse_sitemap(resp.body)
        if kind == "sitemapindex":
            if depth == 0:
                pending.extend((loc, 1) for loc in locs)
            continue
        for loc in locs:
            try:
                link = canonical_url(loc)
            except UnsafeURL:
                continue
            link_parts = urlsplit(link)
            if registrable_domain(link_parts.hostname or "") != domain:
                continue
            if prefix and not (link_parts.path == prefix or link_parts.path.startswith(prefix + "/")):
                continue
            if link in seen:
                continue
            seen.add(link)
            out.append(link)
            if len(out) >= limit:
                return out
    return out


class SiteSource:
    name = "site"

    def __init__(self, client: SafeClient, *, max_links: int, max_new_pages: int) -> None:
        self._client = client
        self._max_links = max_links
        self._max_new_pages = max_new_pages

    async def aclose(self) -> None:
        return None  # the shared SafeClient is closed by aclose_sources()

    def normalize_identifier(self, text: str) -> str:
        value = canonical_url(text)
        if len(value) > 255:
            raise ValueError("URL longa demais.")
        return value

    async def _job_page(self, link: str) -> RawJob | None:
        try:
            page = await self._client.get(link, kind="page")
        except _SKIPPABLE:
            return None
        raws = extract_job_postings(page.text(), base_url=page.url)
        if not raws:
            return None
        return replace(raws[0], external_id=link)

    async def _sitemap_or_empty(self, page_url: str) -> list[str]:
        try:
            return await sitemap_links(self._client, page_url, limit=self._max_links)
        except (SourceBadResponse, SourceUnavailable, RobotsDisallowed):
            return []

    async def fetch_listing(self, url: str, *, known_ids: frozenset[str]) -> SiteFetch:
        page = await self._client.get(url, kind="page")
        html = page.text()
        raws = extract_job_postings(html, base_url=page.url)
        if raws:
            return SiteFetch(raws, frozenset(r.external_id for r in raws))
        hit = detect_embedded_ats(html, page.url)
        if hit is not None:
            raise EmbeddedAtsFound(*hit)
        links = job_links(html, page.url, limit=self._max_links)
        if not links:
            links = await self._sitemap_or_empty(page.url)
        listed = frozenset(links)
        new = [link for link in links if link not in known_ids][: self._max_new_pages]
        out: list[RawJob] = []
        for link in new:
            raw = await self._job_page(link)
            if raw is not None:
                out.append(raw)
        if not out and not any(link in known_ids for link in links):
            raise NoStructuredData(MSG_NO_STRUCTURED)
        return SiteFetch(out, listed)

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        return (await self.fetch_listing(identifier, known_ids=frozenset())).raws

    async def probe(self, url: str, *, extra_paths: tuple[str, ...] = ()) -> ProbeResult:
        base = canonical_url(url)
        candidates = [base] + [urljoin(base, p) for p in extra_paths[:MAX_PROBE_EXTRA]]
        job_budget = PROBE_JOB_PAGES
        for candidate in candidates:
            try:
                target = canonical_url(candidate)
                page = await self._client.get(target, kind="page")
            except _SKIPPABLE:
                continue
            html = page.text()
            hit = detect_embedded_ats(html, page.url)
            if hit is not None:
                return ProbeResult("ats", hit[0], hit[1], [])
            raws = extract_job_postings(html, base_url=page.url)
            if raws:
                return ProbeResult("jsonld", None, page.url, raws)
            for link in job_links(html, page.url, limit=PROBE_JOB_PAGES):
                if job_budget <= 0:
                    break
                job_budget -= 1
                raw = await self._job_page(link)
                if raw is not None:
                    return ProbeResult("jsonld", None, page.url, [raw])
        return ProbeResult("none", None, None, [])
