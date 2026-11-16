from dataclasses import dataclass, field

from rumo_core.discovery.validate import CAREER_PATHS, Outcome, validate_site_candidate
from rumo_core.sources.base import RawJob
from rumo_core.sources.careers import ProbeResult


def raw(eid: str) -> RawJob:
    return RawJob(
        external_id=eid, title=f"Vaga {eid}", url=f"https://acme.test/jobs/{eid}",
        location_text="Remote", description_html="<p>x</p>", department=None,
        posted_at=None, raw={"id": eid},
    )


@dataclass
class FakeSite:
    result: ProbeResult
    calls: list = field(default_factory=list)

    async def probe(self, url, *, extra_paths=()):
        self.calls.append((url, extra_paths))
        return self.result


@dataclass
class FakeAts:
    jobs: list
    calls: list = field(default_factory=list)

    async def fetch_jobs(self, identifier):
        self.calls.append(identifier)
        return list(self.jobs)


async def test_site_candidate_resolves_embedded_ats():
    site = FakeSite(ProbeResult("ats", "lever", "acme", []))
    ats = FakeAts([raw("1")])
    out = await validate_site_candidate(
        site, "acme.test", source_getter=lambda name: ats,
    )
    assert out.kind == "valid"
    assert out.resolved == ("lever", "acme")
    assert ats.calls == ["acme"]
    assert site.calls[0][1] == CAREER_PATHS


async def test_site_candidate_embedded_ats_without_jobs_is_invalid():
    site = FakeSite(ProbeResult("ats", "lever", "acme", []))
    out = await validate_site_candidate(site, "acme.test", source_getter=lambda name: FakeAts([]))
    assert out == Outcome("invalid", 0, None)


async def test_site_candidate_jsonld_creates_site_company():
    page = "https://acme.test/careers"
    site = FakeSite(ProbeResult("jsonld", None, page, [raw("1"), raw("2")]))
    out = await validate_site_candidate(site, "acme.test")
    assert out.kind == "valid" and out.open_jobs == 2
    assert out.resolved == ("site", page)


async def test_site_candidate_jsonld_refused_by_filters_is_invalid():
    site = FakeSite(ProbeResult("jsonld", None, "https://acme.test/careers", [raw("1")]))
    out = await validate_site_candidate(site, "acme.test", accept=lambda r: False)
    assert out == Outcome("invalid", 0, None)


async def test_site_candidate_none_invalid():
    site = FakeSite(ProbeResult("none", None, None, []))
    out = await validate_site_candidate(site, "acme.test")
    assert out == Outcome("invalid", 0, None)


async def test_portal_domain_never_probed():
    site = FakeSite(ProbeResult("none", None, None, []))
    out = await validate_site_candidate(site, "www.vagas.com.br")
    assert out.kind == "invalid"
    assert site.calls == []
