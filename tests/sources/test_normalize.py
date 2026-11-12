import json
import time
from dataclasses import FrozenInstanceError, fields
from decimal import Decimal
from pathlib import Path

import pytest

from rumo_core.sources.base import RawJob
from rumo_core.sources.greenhouse import _to_raw_job
from rumo_core.sources.normalize import (
    NormalizedJob,
    SalaryInfo,
    detect_language,
    detect_seniority,
    detect_work_model,
    extract_salary,
    has_pay_context,
    html_to_text,
    normalize_job,
    parse_location,
)

FIX = Path(__file__).parent.parent / "fixtures" / "greenhouse"
D = Decimal


def fixture_jobs(name):
    data = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    return [_to_raw_job(item) for item in data["jobs"]]


# ---------------------------------------------------------------- html_to_text
@pytest.mark.parametrize(
    "html, expected",
    [
        ("", ""),
        (None, ""),
        ("<p>Hello</p><script>alert(1)</script><style>p{x:y}</style><p>World</p>", "Hello\n\nWorld"),
        ("<noscript>no</noscript><template><b>t</b></template>ok", "ok"),
        ("<ul><li>One</li><li>Two &amp; three</li></ul>", "- One\n- Two & three"),
        ("<ul><li><p>Para in li</p></li></ul>", "- Para in li"),
        ("a&nbsp;&nbsp;b &lt;c&gt;", "a b <c>"),
        ("line1<br>line2<br/>line3", "line1\nline2\nline3"),
        ("<h2>Title</h2><div>  lots   of \n space </div>", "Title\n\nlots of space"),
        ('<a href="https://t.example/x?utm=1">link text</a>', "link text"),
    ],
)
def test_html_to_text(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize(
    "html",
    ["<p>unclosed <b>bold", "<<<>>>&&&;", "<div><p></div></p></span>", "<script>never closed", "</li></ul>text", "<a href='x"],
)
def test_html_to_text_malformed_never_raises(html):
    assert isinstance(html_to_text(html), str)


def test_html_to_text_real_fixture_snippet():
    raw = fixture_jobs("gitlab")[0].description_html
    text = html_to_text(raw)
    assert "<" not in text and "&lt;" not in text and "&amp;" not in text
    assert "Salary Range" in text and "\n- " in text


# -------------------------------------------------------------- parse_location
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Berlin, Germany", ("DE", "Berlin")),
        ("São Paulo, SP, Brasil", ("BR", "São Paulo")),
        ("New York, NY", ("US", "New York")),
        ("Austin, TX, United States", ("US", "Austin")),
        ("Remote - US", ("US", None)),
        ("Remote (Brazil)", ("BR", None)),
        ("US-Remote", ("US", None)),
        ("Remote, United States", ("US", None)),
        ("Remote, Canada; Remote, United States", (None, None)),
        ("Remote, EMEA", (None, None)),
        ("Remote", (None, None)),
        ("Anywhere", (None, None)),
        ("Worldwide", (None, None)),
        ("", (None, None)),
        ("   ", (None, None)),
        ("Springfield", (None, None)),
        ("Springfield, IL", (None, None)),
        ("Berlin or Remote", (None, None)),
        ("Berlin, DE", ("DE", "Berlin")),
        ("San Francisco, CA | Seattle, WA", ("US", None)),
        ("San Francisco, CA", ("US", "San Francisco")),
        ("Toronto, CA", ("CA", "Toronto")),
        ("Dublin, IE", ("IE", "Dublin")),
        ("Munich, Deutschland", ("DE", "Munich")),
        ("Zürich, Schweiz", ("CH", "Zürich")),
        ("Wien, Österreich", ("AT", "Wien")),
        ("Amsterdam, Países Baixos", ("NL", "Amsterdam")),
        ("London, England", ("GB", "London")),
        ("Boston, U.S.", ("US", "Boston")),
        ("Lisboa, Portugal / Remote", ("PT", "Lisboa")),
        ("CA", (None, None)),
        ("Narnia, Atlantis", (None, None)),
        ("Singapore", ("SG", None)),
    ],
)
def test_parse_location(text, expected):
    assert parse_location(text) == expected


# ------------------------------------------------------------ detect_work_model
@pytest.mark.parametrize(
    "loc, title, desc, expected",
    [
        ("", "Backend Engineer (Remote)", "", "remote"),
        ("Remote, Canada", "Engineer", "", "remote"),
        ("Berlin", "Engineer - Hybrid", "", "hybrid"),
        ("Hybrid – 3 days in office", "Engineer", "", "hybrid"),
        ("São Paulo - Presencial", "Engenheiro", "", "onsite"),
        ("Remoto", "Engenheiro de Software", "", "remote"),
        ("München, vor Ort", "Entwickler", "", "onsite"),
        ("Berlin (On-site)", "Engineer (Remote)", "", "unknown"),
        ("Remote or Hybrid", "Engineer", "", "unknown"),
        ("Berlin", "Engineer", "", "unknown"),
        ("Berlin", "Engineer", "We are remote-friendly and offer remote work options.", "unknown"),
        ("Berlin", "Engineer", "Great remote-friendly culture! Benefits include remote work stipend.", "unknown"),
        ("Berlin", "Engineer", "This is a fully remote position.", "remote"),
        ("Berlin", "Engineer", "This role is 100% remote across the EU.", "remote"),
        ("Berlin", "Engineer", "This is not a fully remote role.", "unknown"),
        ("Berlin", "Engineer", "This is a hybrid role, 2 days per week.", "hybrid"),
        ("Lisboa", "Engenheiro", "Vaga presencial em Lisboa.", "onsite"),
        ("Berlin", "Engineer", "On-site required, five days.", "onsite"),
        ("Berlin", "Engineer", "This is a fully remote role. Also a hybrid role is available.", "unknown"),
        ("Remote, US", "Engineer", "On-site required.", "remote"),
    ],
)
def test_detect_work_model(loc, title, desc, expected):
    assert detect_work_model(loc, title, desc) == expected


# ------------------------------------------------------------- detect_seniority
@pytest.mark.parametrize(
    "title, expected",
    [
        ("Sr. Backend Engineer", "senior"),
        ("Senior Software Engineer", "senior"),
        ("Engenheiro de Software Sênior", "senior"),
        ("Engenheiro de Software Pleno", "mid"),
        ("Desenvolvedor Júnior", "junior"),
        ("Jr. Data Analyst", "junior"),
        ("Software Engineering Intern", "intern"),
        ("Estagiário de Dados", "intern"),
        ("International Sales Representative", "unknown"),
        ("Leadership Development Program Coordinator", "unknown"),
        ("Misleading Title Specialist", "unknown"),
        ("Staff Software Engineer", "lead"),
        ("Principal Engineer", "lead"),
        ("Team Lead", "lead"),
        ("Backend Team Lead (Berlin)", "lead"),
        ("Tech Lead, Payments", "lead"),
        ("Lead Software Engineer", "lead"),
        ("Lead Generation Specialist", "unknown"),
        ("Engineering Manager", "manager"),
        ("Senior Manager, Finance", "manager"),
        ("Gerente de Produto", "manager"),
        ("Head of Data", "director"),
        ("VP of Engineering", "director"),
        ("Senior Director, Sales", "director"),
        ("Software Engineer", "unknown"),
        ("Mid-Market Account Executive", "unknown"),
        ("Staff Accountant", "unknown"),
        ("", "unknown"),
    ],
)
def test_detect_seniority(title, expected):
    assert detect_seniority(title) == expected


# -------------------------------------------------------------- detect_language
EN = "We are looking for an engineer to join our team. You will work with the product group and help us build the platform that our customers use every day. This is a role for someone who loves to learn."
PT = "Estamos à procura de um engenheiro para se juntar ao nosso time. Você vai trabalhar com o grupo de produto e ajudar a construir a plataforma que os nossos clientes usam todos os dias. Essa vaga é para quem gosta de aprender."
DE = "Wir suchen einen Entwickler für unser Team. Du arbeitest mit der Produktgruppe zusammen und hilfst uns, die Plattform zu bauen, die unsere Kunden jeden Tag nutzen. Diese Stelle ist für alle, die gerne lernen und nicht stehen bleiben."


@pytest.mark.parametrize(
    "text, expected",
    [
        (EN, "en"),
        (PT, "pt"),
        (DE, "de"),
        ("", "unknown"),
        ("Engineer", "unknown"),
        ("Backend Engineer", "unknown"),
        ("Senior Software Engineer, Payments Platform", "unknown"),
        ("Python Django AWS Kubernetes Docker Terraform " * 10, "unknown"),
        ("Wir the team ist and our der work " * 4, "unknown"),
        ("Estamos com uma vaga para o time de dados e você será responsável por tudo.", "pt"),
        (EN + "\n" + PT[:60], "en"),
        ("x" * 3000 + EN, "unknown"),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_language_is_text_based_not_country_based():
    job = RawJob("1", "Backend Engineer", "http://x", "Berlin, Germany", f"<p>{EN}</p>", None, None, {})
    n = normalize_job(job)
    assert n.country == "DE" and n.language == "en"


# --------------------------------------------------------------- extract_salary
def sal(text):
    s = extract_salary(text)
    return None if s is None else (s.min, s.max, s.currency, s.period)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Salary: $120k–$150k", (D(120000), D(150000), "USD", None)),
        ("$120,000 - $150,000 per year", (D(120000), D(150000), "USD", "year")),
        ("The range is $120-150k a year.", (D(120000), D(150000), "USD", "year")),
        ("€60.000 – 80.000 pro Jahr", (D(60000), D(80000), "EUR", "year")),
        ("Gehalt: 60.000 bis 80.000 € brutto", (D(60000), D(80000), "EUR", None)),
        ("Salário: R$ 8.000 por mês", (D(8000), None, "BRL", "month")),
        ("Salário: R$ 8.000 a R$ 10.000", (D(8000), D(10000), "BRL", None)),
        ("Salary: $45/hour", (D(45), None, "USD", "hour")),
        ("Compensation of $45.50 per hour", (D("45.50"), None, "USD", "hour")),
        ("Pay range 1.000,50 EUR monthly", (D("1000.50"), None, "EUR", "month")),
        ("Salary £50,000 to £60,000", (D(50000), D(60000), "GBP", None)),
        ("Salary: USD 90,000 - 110,000", (D(90000), D(110000), "USD", None)),
        ("Salary CAD $90,000 - $110,000", (D(90000), D(110000), "CAD", None)),
        ("Salary: A$100k-120k", (D(100000), D(120000), "AUD", None)),
        ("Annual base salary $100,000. Bonus up to $20,000.", (D(100000), None, "USD", None)),
        ("Base $100k-$120k, plus equity of $50k", (D(100000), D(120000), "USD", None)),
        ("The founding team raised $5M. Salary $100k-$120k", (D(100000), D(120000), "USD", None)),
        ("$80k-$100k then $120k-$140k", (D(80000), D(100000), "USD", None)),
        # must be None
        ("401k match and great benefits", None),
        ("10 years of experience required", None),
        ("We raised $2B in funding", None),
        ("Valued at $1B, ARR over $500M", None),
        ("Salary is competitive", None),
        ("120,000 - 150,000 per year", None),
        ("Founded in 2015, 30% growth", None),
        ("Equity: $100k–$200k in RSUs", None),
        ("Annual bonus $10,000-$20,000 bonus", None),
        ("Gift card worth $50", None),
        ("Salary $150k-$120k", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_salary(text, expected):
    assert sal(text) == expected


def test_extract_salary_returns_salaryinfo_with_decimals():
    s = extract_salary("Salary $100k-$120k")
    assert isinstance(s, SalaryInfo) and isinstance(s.min, Decimal)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Competitive salary", True),
        ("Compensation and benefits", True),
        ("Pay range: tbd", True),
        ("Gehalt nach Vereinbarung", True),
        ("Salário compatível", True),
        ("Remuneração a combinar", True),
        ("Paid $45/hour", False),
        ("annual leave, hourly updates, per year", False),
        ("Vergütung nach Absprache", True),
        ("We love pets and remote work", False),
        ("", False),
    ],
)
def test_has_pay_context(text, expected):
    assert has_pay_context(text) is expected


# ---------------------------------------------------------------- normalize_job
def make_raw(**kw):
    base = dict(
        external_id="1", title="Senior Backend Engineer", url="http://x/1",
        location_text="Berlin, Germany", description_html=f"<p>{EN}</p><p>Salary: $100k-$120k</p>",
        department="Engineering", posted_at=None, raw={},
    )  # fmt: skip
    base.update(kw)
    return RawJob(**base)


def test_normalize_job_fields():
    n = normalize_job(make_raw())
    assert [f.name for f in fields(NormalizedJob)] == [
        "description", "country", "city", "work_model", "seniority", "language", "department",
        "salary_min", "salary_max", "salary_currency", "salary_period", "salary_known",
        "content_hash", "fingerprint",
    ]  # fmt: skip
    assert (n.country, n.city, n.work_model, n.seniority, n.language) == ("DE", "Berlin", "unknown", "senior", "en")
    assert n.department == "Engineering"
    assert (n.salary_min, n.salary_max, n.salary_currency, n.salary_known) == (D(100000), D(120000), "USD", True)
    assert n.description.startswith("We are looking")
    assert len(n.content_hash) == 64 and len(n.fingerprint) == 64
    with pytest.raises(FrozenInstanceError):
        n.country = "US"  # type: ignore[misc]


def test_content_hash_stable_and_sensitive():
    a, b = normalize_job(make_raw()), normalize_job(make_raw())
    assert a.content_hash == b.content_hash
    assert normalize_job(make_raw(description_html="<p>Different</p>")).content_hash != a.content_hash
    assert normalize_job(make_raw(title="Other")).content_hash != a.content_hash
    assert normalize_job(make_raw(location_text="Paris")).content_hash != a.content_hash
    assert normalize_job(make_raw(department="Sales")).content_hash != a.content_hash
    assert normalize_job(make_raw(department=None)).content_hash != a.content_hash
    assert normalize_job(make_raw(external_id="99", url="http://y")).content_hash == a.content_hash


def test_fingerprint_ignores_id_description_and_punctuation():
    a = normalize_job(make_raw())
    b = normalize_job(make_raw(external_id="2", description_html="<p>x</p>", title="senior  backend engineer!"))
    assert a.fingerprint == b.fingerprint
    assert normalize_job(make_raw(location_text="Paris, France")).fingerprint != a.fingerprint
    assert normalize_job(make_raw(title="Junior Backend Engineer")).fingerprint != a.fingerprint


def test_normalize_job_empty_description():
    n = normalize_job(make_raw(description_html="", location_text=""))
    assert n.description == "" and n.language == "unknown" and not n.salary_known
    assert n.country is None and n.salary_min is None and n.salary_currency is None


# ---------------------------------------------------------------- real fixtures
@pytest.mark.parametrize("name", ["gitlab", "anthropic"])
def test_normalize_all_real_fixture_jobs(name, capsys):
    jobs = fixture_jobs(name)
    assert len(jobs) == 7
    for raw in jobs:
        n = normalize_job(raw)
        if raw.description_html.strip():
            assert n.description
        assert n.work_model in {"remote", "hybrid", "onsite", "unknown"}
        assert n.salary_known == (n.salary_min is not None)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_real_fixture_concrete_expectations():
    gl = {(j.title, j.location_text): normalize_job(j) for j in fixture_jobs("gitlab")}
    n = gl[("Associate Renewals Manager, SMB", "Remote, Canada; Remote, United States")]
    # two countries in the location -> unknown (the salary is US-only)
    assert (n.country, n.work_model, n.seniority, n.language) == (None, "remote", "manager", "en")
    assert n.department == "Renewals"
    assert n.salary_known and n.salary_currency == "USD" and n.salary_min == D(53760) and n.salary_max == D(105000)
    n = gl[("Backend Engineer (Ruby), Plan: Spec-Driven Development", "Remote, United Kingdom")]
    assert (n.country, n.city, n.work_model, n.seniority) == ("GB", None, "remote", "unknown")
    n = gl[("Backend Engineer (Ruby), Plan: Spec-Driven Development", "Remote, Poland")]
    assert (n.country, n.salary_currency, n.salary_min, n.salary_max) == ("PL", "PLN", D(272000), D(408000))

    an = {(j.title, j.location_text): normalize_job(j) for j in fixture_jobs("anthropic")}
    n = an[("Account Executive, Startups", "Dublin, IE")]
    assert (n.country, n.city, n.salary_currency, n.salary_min, n.salary_max) == ("IE", "Dublin", "EUR", D(155000), D(205000))
    n = an[("Account Executive - Public Sector (ASEAN)", "Singapore")]
    assert n.country == "SG" and not n.salary_known
    n = an[("Activation Manager, Beneficial Deployments", "San Francisco, CA | New York City, NY")]
    assert (n.country, n.city, n.seniority) == ("US", None, "manager")


# ------------------------------------------------- fix round 1: new behaviours
@pytest.mark.parametrize(
    "text",
    [
        "USA, Canada", "Remote (US, Canada)", "Germany, Austria, Switzerland", "Brazil, Argentina",
        "Spain, Portugal", "UK, Ireland", "Berlin, Germany; Paris, France", "Remote - US or Canada",
        "Canada, Germany", "Germany, NY", "Remote, US; Remote, CA",
    ],
)
def test_parse_location_multi_country_is_unknown(text):
    assert parse_location(text) == (None, None)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Berlin; Munich, Germany", (None, None)),
        ("Remote, Germany; Remote, Germany", ("DE", None)),
        ("Berlin, Germany; Berlin, Germany", ("DE", "Berlin")),
        ("Germany, Berlin", (None, None)),
    ],
)
def test_parse_location_same_country_repeated(text, expected):
    assert parse_location(text) == expected


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Hybrid Cloud Engineer", "unknown"),
        ("Remote Support Engineer", "unknown"),
        ("Remote Sensing Scientist", "unknown"),
        ("Remote Monitoring Technician", "unknown"),
        ("Remotely Operated Vehicle Pilot", "unknown"),
        ("Scientist, Remote Sensing", "unknown"),
        ("Engineer (Remote)", "remote"),
        ("Engineer [Hybrid]", "hybrid"),
        ("Engineer - Remote", "remote"),
        ("Engineer | Hybrid", "hybrid"),
        ("Engineer, Remote", "remote"),
        ("Engineer Remote", "remote"),
        ("Remote - Senior Engineer", "remote"),
        ("Engenheiro Híbrida", "hybrid"),
    ],
)
def test_work_model_title_false_positives(title, expected):
    assert detect_work_model("", title, "") == expected


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("We build tools for fully remote teams.", "unknown"),
        ("Our customers are 100% remote teams.", "unknown"),
        ("We are a fully remote company; this role is based in our Berlin office.", "unknown"),
        ("You can work fully remote on Fridays.", "unknown"),
        ("This role is remote-friendly.", "unknown"),
        ("This role is fully remote.", "remote"),
        ("This is a 100% remote position.", "remote"),
        ("A fully remote role with flexible hours.", "remote"),
        ("This is a remote position.", "remote"),
        ("Remote-first role.", "remote"),
        ("Esta vaga é 100% remota.", "remote"),
        ("Diese Stelle ist komplett remote.", "remote"),
        ("Vaga híbrida em São Paulo.", "hybrid"),
        ("This role is not remote.", "unknown"),
        ("This is not a fully remote role.", "unknown"),
    ],
)
def test_work_model_description_requires_role_statement(desc, expected):
    assert detect_work_model("Berlin", "Engineer", desc) == expected


@pytest.mark.parametrize(
    "title",
    ["Chief of Staff", "Assistant to the Director", "Director's Assistant", "Executive Assistant to the VP", "Assistente do Diretor"],
)
def test_seniority_exclusions(title):
    assert detect_seniority(title) == "unknown"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Compensation: $100,000 - $120,000 + equity", (D(100000), D(120000), "USD", None)),
        ("Salary $100,000 - $120,000 + $20k bonus", (D(100000), D(120000), "USD", None)),
        ("Salary: CHF 100'000 - 120'000", (D(100000), D(120000), "CHF", None)),
        ("Gehalt: 60 000 - 80 000 EUR", (D(60000), D(80000), "EUR", None)),
        ("Salary: USD$100,000-$120,000", (D(100000), D(120000), "USD", None)),
        ("Gehalt 55.000€ - 65.000€", (D(55000), D(65000), "EUR", None)),
        ("Salary: 272,000 - 408,000 PLN", (D(272000), D(408000), "PLN", None)),
        ("Poland base salary range: 248,000zł - 372,000zł", (D(248000), D(372000), "PLN", None)),
        ("Salary 800 000 - 900 000 SEK", (D(800000), D(900000), "SEK", None)),
        ("Salary $100,000. Learning budget $2,000-$3,000.", (D(100000), None, "USD", None)),
        # must be None
        ("$1,500 learning budget", None),
        ("Learning budget of $1,000-$2,000 per year", None),
        ("$2,000 home office stipend", None),
        ("Relocation allowance: $5,000 - $10,000", None),
        ("We offer a $500 wellness allowance, salary competitive", None),
        ("Gift cards of $50-$100 per year", None),
        ("Salary up to $150k", None),
        ("Gehalt bis zu 90.000 €", None),
        ("Salário até R$ 12.000", None),
        ("Salary: maximum $200,000", None),
        ("The role pays $100,000 per year", None),
        ("Annual $100,000", None),
        ("$100 20 widgets salary", None),
    ],
)
def test_extract_salary_fix_round(text, expected):
    assert sal(text) == expected


def test_extract_salary_conflicting_currency_ranges_is_none():
    assert extract_salary("Salary: $100k-$120k USD. Poland: 300,000 - 400,000 PLN") is None


def test_html_to_text_structure_fixes():
    assert html_to_text("<table><tr><td>a</td><td>b</td></tr></table>") == "a b"
    assert html_to_text("<ul><li></li><li>x</li><li> </li></ul>after") == "- x\n\nafter"
    assert html_to_text("<ul><li></li></ul><p>Next</p>") == "Next"
    assert html_to_text("<ul><li>Parent<ul><li>Child</li></ul></li><li>Two</li></ul>") == "- Parent\n- Child\n- Two"
    assert "\x01" not in html_to_text("<li>a\x01b</li>")


def test_fingerprint_keeps_plus_and_hash():
    fps = {normalize_job(make_raw(title=t)).fingerprint for t in ("C++ Engineer", "C Engineer", "C# Engineer")}
    assert len(fps) == 3


def test_content_hash_field_boundaries_do_not_collide():
    a = normalize_job(make_raw(title="a\x1fb", location_text="c")).content_hash
    b = normalize_job(make_raw(title="a", location_text="b\x1fc")).content_hash
    assert a != b


def test_normalize_job_handles_none_fields():
    n = normalize_job(make_raw(location_text=None, title=None, department=None))
    assert n.country is None and n.seniority == "unknown"


# ------------------------------------------------------------ timing / ReDoS
BIG = 200_000


@pytest.mark.parametrize(
    "payload",
    [
        "1" * BIG,
        "$1 " * (BIG // 3),
        "a" + " " * BIG + "-(remote!",
        " " * BIG + "x",
        "(" * BIG,
        "<div>" * (BIG // 5),
        "<li>" * (BIG // 4) + "x" + "</li>" * (BIG // 5),
        "&" * BIG,
        "$1,1" * (BIG // 4),
        "1 " * (BIG // 2) + "EUR",
        "Remote " * (BIG // 7),
        "salary " * (BIG // 7) + "$1",
    ],
    ids=["digits", "dollar1", "spaces-dash-remote", "ws-letter", "parens", "nested-div", "nested-li", "amp", "dollar-comma", "digit-space", "remote-rep", "salary-rep"],
)
def test_public_functions_are_fast_on_adversarial_input(payload):
    start = time.perf_counter()
    html_to_text(payload)
    parse_location(payload)
    detect_work_model(payload, payload, payload)
    detect_seniority(payload)
    detect_language(payload)
    extract_salary(payload)
    has_pay_context(payload)
    normalize_job(make_raw(title=payload, location_text=payload, description_html=payload, department=payload))
    assert time.perf_counter() - start < 1.5


# ------------------------------------------------- fix round 2
@pytest.mark.parametrize(
    "text",
    ["Toronto, ON / Austin, TX", "Austin, TX; Toronto, ON", "Austin, TX / London", "Berlin; Munich, Germany"],
)
def test_parse_location_unresolved_part_in_multi_location_is_unknown(text):
    assert parse_location(text) == (None, None)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hybrid - Berlin, Germany", ("DE", "Berlin")),
        ("On-site - Austin, TX", ("US", "Austin")),
        ("Hybrid: London, UK", ("GB", "London")),
        ("Berlin, Germany (Hybrid)", ("DE", "Berlin")),
        ("Presencial - São Paulo, Brasil", ("BR", "São Paulo")),
    ],
)
def test_parse_location_strips_work_model_prefix(text, expected):
    assert parse_location(text) == expected


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("This is a hybrid of research and engineering.", "unknown"),
        ("This is an on-site interview process.", "unknown"),
        ("This is a hybrid role.", "hybrid"),
        ("This is an on-site role.", "onsite"),
        ("The position is remote.", "remote"),
        ("The role is fully remote.", "remote"),
        ("The role is on-site.", "onsite"),
        ("The position is hybrid.", "hybrid"),
        ("The role is remote friendly.", "unknown"),
    ],
)
def test_work_model_description_needs_role_noun(desc, expected):
    assert detect_work_model("Berlin", "Engineer", desc) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Eligible for $10,000 - $15,000 annual performance bonus", None),
        ("$20,000 - $30,000 yearly performance bonus, salary $100,000-$120,000", (D(100000), D(120000), "USD", None)),
        ("$100,000-$120,000 annual target bonus", None),
        ("Salary range: $80/hr - $100/hr", (D(80), D(100), "USD", "hour")),
        ("Salary $100,000 salary plus stipend", (D(100000), None, "USD", None)),
        ("Base $100k-$120k, plus equity of $50k", (D(100000), D(120000), "USD", None)),
    ],
)
def test_extract_salary_round2(text, expected):
    assert sal(text) == expected


def test_html_sanitizer_keeps_normal_markup():
    assert html_to_text('<p class="x" title="ab">Hello <b>big</b> &amp; 1 < 2</p><!-- c --><p>Next<br/>line</p>').startswith("Hello big & 1 < 2")
    assert html_to_text("a <!-- never closed") == "a"
    assert html_to_text("x<y and <z") == "x<y and <z"


@pytest.mark.parametrize(
    "payload",
    [
        "<a " * 70_000, "<!--" * 50_000, '<a href="' * 30_000, "<div" * 60_000, "<a x='>" * 30_000, '<a x=">' * 30_000,
        "<script>" * 30_000, "<style>" * 30_000, "<![CDATA[" * 20_000, "123 " * 50_000, "100'000 " * 25_000,
        "Salary: " + "100 000 " * 25_000, "1 000" * 40_000, "$1,000 " * 28_000, "1.000.000.000," * 14_000,
    ],
    ids=["a-open", "comment-open", "href-open", "div-open", "squote", "dquote", "script-open", "style-open", "cdata", "3digit-chain", "apos-chain", "salary-space-chain", "nbsp-chain", "dollar-groups", "dot-chain"],
)
def test_round2_adversarial_shapes_are_fast(payload):
    start = time.perf_counter()
    html_to_text(payload)
    extract_salary(payload)
    parse_location(payload)
    detect_work_model(payload, payload, payload)
    normalize_job(make_raw(title=payload, location_text=payload, description_html=payload))
    assert time.perf_counter() - start < 1.5
