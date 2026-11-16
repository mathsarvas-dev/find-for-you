import pytest

from rumo_core.sources import ATS_HOSTS, ats_from_url, get_source
from rumo_core.sources.base import ATS_SOURCES, source_rank_for


def test_every_ats_source_registered():
    for name in sorted(ATS_SOURCES):
        assert get_source(name).name == name


def test_every_ats_source_has_hosts():
    assert set(ATS_HOSTS) == set(ATS_SOURCES)


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
    ("https://jobs.lever.co/leverdemo", ("lever", "leverdemo")),
    ("https://jobs.ashbyhq.com/Ashby", ("ashby", "ashby")),
    ("https://jobs.smartrecruiters.com/smartrecruiters/744000158320111-x", ("smartrecruiters", "smartrecruiters")),
    ("https://breezy.breezy.hr/p/123-vaga", ("breezy", "breezy")),
    ("https://techpartnerships.recruitee.com/o/front", ("recruitee", "techpartnerships")),
    ("https://dci.jobs.personio.de/job/1", ("personio", "dci")),
    ("https://kompan.teamtailor.com/jobs", ("teamtailor", "kompan")),
    ("https://semble.bamboohr.com/careers/list", ("bamboohr", "semble")),
    ("https://carto.pinpointhq.com/postings.json", ("pinpoint", "carto")),
])
def test_ats_from_url_hosted(url, expected):
    assert ats_from_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://example.com/careers",
    "https://evil.test/?x=greenhouse.io",
    "https://notlever.co/acme",
    "",
    "not a url",
])
def test_ats_from_url_rejects_others(url):
    assert ats_from_url(url) is None


def test_source_rank_for():
    assert source_rank_for("greenhouse") == 0
    assert source_rank_for("site") == 1
    assert source_rank_for("link") == 1
    assert source_rank_for("remotive") == 2
