import json
from urllib.parse import urlsplit

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse, SourceBlocked
from rumo_core.sources.careers import (
    MSG_NO_STRUCTURED,
    EmbeddedAtsFound,
    NoStructuredData,
    SiteSource,
    detect_embedded_ats,
    job_links,
    sitemap_links,
)
from rumo_core.sources.http import SafeClient, UnsafeURL

PUBLIC = "93.184.216.34"
PAGE = "https://jobs.example.com/careers"


def _resolver(private=()):
    async def resolve(host, port):
        return ["10.0.0.1"] if host in private else [PUBLIC]
    return resolve


async def _no_sleep(seconds):
    return None


def _zero():
    return 0.0


def _posting(n: int, host: str = "jobs.example.com") -> str:
    data = {
        "@type": "JobPosting",
        "title": f"Vaga {n}",
        "url": f"https://{host}/jobs/{n}",
        "identifier": f"id-{n}",
        "description": f"<p>Descrição {n}</p>",
        "datePosted": "2026-11-10",
    }
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def _html(body: str, status: int = 200):
    return (status, "text/html; charset=utf-8", f"<html><body>{body}</body></html>")


def _client(routes: dict, calls: list, private=()):
    def handler(request):
        key = request.headers.get("host", "") + request.url.path
        calls.append(key)
        spec = routes.get(key)
        if spec is None and callable(routes.get("*")):
            spec = routes["*"](key)
        if spec is None:
            spec = (404, "text/html", "")
        status, ctype, body = spec
        return httpx.Response(status, headers={"content-type": ctype}, content=body.encode())

    return SafeClient(
        httpx.MockTransport(handler), resolve=_resolver(private), sleep=_no_sleep, clock=_zero,
    )


def _src(client, **kw):
    return SiteSource(client, max_links=kw.pop("max_links", 200),
                      max_new_pages=kw.pop("max_new_pages", 25))


async def test_jsonld_on_listing_page_no_link_following():
    page = _html(_posting(1) + _posting(2) + '<a href="/jobs/9">mais</a>')
    calls: list[str] = []
    fetch = await _src(_client({"jobs.example.com/careers": page}, calls)).fetch_listing(
        PAGE, known_ids=frozenset(),
    )
    assert len(fetch.raws) == 2
    assert fetch.listed_ids == frozenset({"id-1", "id-2"})
    assert "jobs.example.com/jobs/9" not in calls


async def test_detects_embedded_greenhouse_iframe():
    page = _html('<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>')
    with pytest.raises(EmbeddedAtsFound) as info:
        await _src(_client({"jobs.example.com/careers": page}, [])).fetch_listing(
            PAGE, known_ids=frozenset(),
        )
    assert info.value.source == "greenhouse"
    assert info.value.identifier == "acme"


async def test_detects_lever_link():
    page = '<a href="https://jobs.lever.co/leverdemo">Vagas</a>'
    assert detect_embedded_ats(page, PAGE) == ("lever", "leverdemo")


async def test_follows_same_domain_job_links_only():
    page = _html(
        '<a href="/jobs/1">1</a><a href="/jobs/2">2</a><a href="/careers/3">3</a>'
        '<a href="https://other.test/jobs/9">x</a><a href="mailto:a@b.co">m</a>'
    )
    routes = {"jobs.example.com/careers": page}
    for n in (1, 2):
        routes[f"jobs.example.com/jobs/{n}"] = _html(_posting(n))
    routes["jobs.example.com/careers/3"] = _html(_posting(3))
    calls: list[str] = []
    fetch = await _src(_client(routes, calls)).fetch_listing(PAGE, known_ids=frozenset())
    assert len(fetch.raws) == 3
    fetched = [c for c in calls if c.startswith("jobs.example.com/jobs/") or "/careers/3" in c]
    assert len(fetched) == 3
    assert not any("other.test" in c for c in calls)


async def test_link_cap_and_new_page_budget():
    links = "".join(f'<a href="/jobs/{n}">v{n}</a>' for n in range(250))
    routes = {"*": lambda key: _html(_posting(int(key.rsplit("/", 1)[-1])))
              if "/jobs/" in key else None}
    routes["jobs.example.com/careers"] = _html(links)
    calls: list[str] = []
    fetch = await _src(_client(routes, calls)).fetch_listing(PAGE, known_ids=frozenset())
    assert len(fetch.listed_ids) == 200
    assert sum(1 for c in calls if "/jobs/" in c) == 25

    known = frozenset(f"https://jobs.example.com/jobs/{n}" for n in range(25))
    calls2: list[str] = []
    await _src(_client(routes, calls2)).fetch_listing(PAGE, known_ids=known)
    fetched = sorted(int(c.rsplit("/", 1)[-1]) for c in calls2 if "/jobs/" in c)
    assert fetched == list(range(25, 50))


async def test_sitemap_used_when_no_links():
    robots = (200, "text/plain", "User-agent: *\nAllow: /\nSitemap: https://jobs.example.com/sitemap.xml\n")
    sitemap = (200, "application/xml",
               '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               "<url><loc>https://jobs.example.com/careers/a</loc></url>"
               "<url><loc>https://jobs.example.com/careers/b</loc></url>"
               "<url><loc>https://jobs.example.com/blog/x</loc></url></urlset>")
    routes = {
        "jobs.example.com/robots.txt": robots,
        "jobs.example.com/sitemap.xml": sitemap,
        "jobs.example.com/careers": _html("<p>sem links</p>"),
        "jobs.example.com/careers/a": _html(_posting(1)),
        "jobs.example.com/careers/b": _html(_posting(2)),
        "jobs.example.com/blog/x": _html(_posting(3)),
    }
    calls: list[str] = []
    fetch = await _src(_client(routes, calls)).fetch_listing(PAGE, known_ids=frozenset())
    assert len(fetch.raws) == 2
    assert "jobs.example.com/blog/x" not in calls


async def test_sitemap_with_doctype_rejected():
    routes = {
        "jobs.example.com/robots.txt": (200, "text/plain", "Sitemap: https://jobs.example.com/s.xml\n"),
        "jobs.example.com/s.xml": (200, "application/xml",
                                   '<!DOCTYPE x [<!ENTITY a "b">]><urlset></urlset>'),
    }
    client = _client(routes, [])
    with pytest.raises(SourceBadResponse):
        await sitemap_links(client, PAGE, limit=10)


async def test_no_jsonld_raises_no_structured_data():
    with pytest.raises(NoStructuredData) as info:
        await _src(_client({"jobs.example.com/careers": _html("<p>nada</p>")}, [])).fetch_listing(
            PAGE, known_ids=frozenset(),
        )
    assert str(info.value) == MSG_NO_STRUCTURED


async def test_job_page_resolving_private_is_skipped():
    page = _html('<a href="/jobs/1">1</a><a href="https://private.example.com/jobs/2">2</a>')
    routes = {"jobs.example.com/careers": page, "jobs.example.com/jobs/1": _html(_posting(1))}
    client = _client(routes, [], private=("private.example.com",))
    fetch = await _src(client).fetch_listing(PAGE, known_ids=frozenset())
    assert [r.external_id for r in fetch.raws] == ["https://jobs.example.com/jobs/1"]


async def test_careers_url_private_raises_unsafe():
    with pytest.raises(UnsafeURL):
        await _src(_client({}, [])).fetch_listing("http://10.0.0.1/careers", known_ids=frozenset())


async def test_blocked_propagates():
    page = _html('<a href="/jobs/1">1</a><a href="/jobs/2">2</a>')
    routes = {
        "jobs.example.com/careers": page,
        "jobs.example.com/jobs/1": (403, "text/html", ""),
        "jobs.example.com/jobs/2": (403, "text/html", ""),
    }
    with pytest.raises(SourceBlocked):
        await _src(_client(routes, [])).fetch_listing(PAGE, known_ids=frozenset())


async def test_probe_kinds():
    ats = _html('<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>')
    found = await _src(_client({"jobs.example.com/careers": ats}, [])).probe(PAGE)
    assert (found.kind, found.source, found.identifier) == ("ats", "greenhouse", "acme")

    jobs_page = _html('<a href="/jobs/1">1</a>')
    routes = {"jobs.example.com/careers": jobs_page, "jobs.example.com/jobs/1": _html(_posting(1))}
    found = await _src(_client(routes, [])).probe(PAGE)
    assert found.kind == "jsonld"
    assert len(found.raws) == 1

    none = await _src(_client({"jobs.example.com/careers": _html("<p>x</p>")}, [])).probe(
        PAGE, extra_paths=("https://jobs.example.com/vagas",),
    )
    assert none.kind == "none"


def test_job_links_filters_domain_and_keywords():
    html = '<a href="/vagas/9">v</a><a href="/sobre">s</a><a href="https://x.test/vagas/1">x</a>'
    assert job_links(html, PAGE, limit=10) == ["https://jobs.example.com/vagas/9"]
    assert urlsplit(PAGE).hostname == "jobs.example.com"
