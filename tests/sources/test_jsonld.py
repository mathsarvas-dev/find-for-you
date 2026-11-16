import json
from decimal import Decimal

from rumo_core.sources.base import SalaryHint
from rumo_core.sources.jsonld import employer_name, extract_job_postings
from rumo_core.sources.normalize import normalize_job

BASE = "https://jobs.example.com/careers"


def _page(*blocks: str) -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{b}</script>' for b in blocks
    )
    return f"<html><head>{scripts}</head><body><p>hi</p></body></html>"


def _posting(**kw) -> dict:
    base = {
        "@type": "JobPosting",
        "title": "Analista de dados",
        "url": "https://jobs.example.com/careers/1",
        "identifier": {"@type": "PropertyValue", "value": "job-1"},
        "description": "<p>Python e SQL.</p>",
        "datePosted": "2026-11-10",
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "São Paulo",
            "addressRegion": "SP", "addressCountry": "BR"}},
    }
    base.update(kw)
    return base


def test_graph_wrapper():
    data = {"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "name": "Acme"},
        _posting(),
    ]}
    jobs = extract_job_postings(_page(json.dumps(data)), base_url=BASE)
    assert [j.title for j in jobs] == ["Analista de dados"]


def test_multiple_blocks_and_list():
    one = json.dumps([_posting(title="A", identifier="a")])
    two = json.dumps(_posting(title="B", identifier="b"))
    jobs = extract_job_postings(_page(one, two), base_url=BASE)
    assert [j.title for j in jobs] == ["A", "B"]


def test_mixed_types_only_jobposting():
    org = json.dumps({"@type": "Organization", "name": "Acme"})
    jobs = extract_job_postings(_page(org, json.dumps(_posting())), base_url=BASE)
    assert len(jobs) == 1


def test_type_as_list():
    p = _posting(**{"@type": ["Thing", "JobPosting"]})
    assert len(extract_job_postings(_page(json.dumps(p)), base_url=BASE)) == 1


def test_invalid_block_skipped_other_parsed():
    jobs = extract_job_postings(
        _page("{not json", json.dumps(_posting())), base_url=BASE,
    )
    assert len(jobs) == 1


def test_telecommute_with_applicant_country():
    p = _posting(jobLocationType="TELECOMMUTE", jobLocation=None, applicantLocationRequirements={
        "@type": "Country", "name": "Brazil"})
    p.pop("jobLocation")
    job = extract_job_postings(_page(json.dumps(p)), base_url=BASE)[0]
    assert job.work_model_hint == "remote"
    assert job.location_text == "Remote - Brazil"


def test_salary_range_year():
    p = _posting(baseSalary={"@type": "MonetaryAmount", "currency": "USD", "value": {
        "@type": "QuantitativeValue", "minValue": 100000, "maxValue": 120000,
        "unitText": "YEAR"}})
    job = extract_job_postings(_page(json.dumps(p)), base_url=BASE)[0]
    assert job.salary_hint == SalaryHint(Decimal("100000"), Decimal("120000"), "USD", "year")


def test_salary_week_ignored():
    p = _posting(baseSalary={"@type": "MonetaryAmount", "currency": "USD", "value": {
        "@type": "QuantitativeValue", "value": 2000, "unitText": "WEEK"}})
    job = extract_job_postings(_page(json.dumps(p)), base_url=BASE)[0]
    assert job.salary_hint is None


def test_date_only_posted_at_is_aware():
    job = extract_job_postings(_page(json.dumps(_posting())), base_url=BASE)[0]
    assert job.posted_at is not None
    assert job.posted_at.tzinfo is not None
    assert job.posted_at.year == 2026


def test_description_html_with_script_is_data():
    p = _posting(description="<p>Oi</p><script>alert(1)</script><img src=x onerror=alert(2)>")
    # a literal "</script>" would end the ld+json block (as in a browser): escape the slash
    block = json.dumps(p).replace("</", "<\\/")
    job = extract_job_postings(_page(block), base_url=BASE)[0]
    text = normalize_job(job).description
    assert "alert(" not in text


def test_javascript_url_falls_back_to_base_url():
    p = _posting(url="javascript:alert(1)")
    job = extract_job_postings(_page(json.dumps(p)), base_url=BASE)[0]
    assert job.url == BASE


def test_missing_title_skipped():
    p = _posting()
    p.pop("title")
    assert extract_job_postings(_page(json.dumps(p)), base_url=BASE) == []


def test_no_jobposting_returns_empty():
    org = json.dumps({"@type": "Organization", "name": "Acme"})
    assert extract_job_postings(_page(org), base_url=BASE) == []


def test_employer_name():
    p = _posting(hiringOrganization={"@type": "Organization", "name": "  Acme Ltda  "})
    job = extract_job_postings(_page(json.dumps(p)), base_url=BASE)[0]
    assert employer_name(job) == "Acme Ltda"
    q = _posting(hiringOrganization="Beta SA")
    job2 = extract_job_postings(_page(json.dumps(q)), base_url=BASE)[0]
    assert employer_name(job2) == "Beta SA"


def test_long_identifier_hashed_and_country_hint():
    p = _posting(identifier="x" * 300)
    job = extract_job_postings(_page(json.dumps(p)), base_url=BASE)[0]
    assert len(job.external_id) == 64
    assert job.country_hint == "BR"
