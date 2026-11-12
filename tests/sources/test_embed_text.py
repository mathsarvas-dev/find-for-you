from types import SimpleNamespace

from rumo_core.sources.embed_text import DESCRIPTION_MAX_CHARS, job_embedding_text


def mk(**kw):
    base = dict(
        title="Backend Engineer", location_text="Remote, Brazil", city="São Paulo",
        country="BR", work_model="remote", seniority="senior", department="Eng",
        description="Python and Postgres.", salary_min=100, salary_max=200,
        salary_currency="BRL",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_fixed_order_and_labels():
    t = job_embedding_text(mk(), "Acme")
    labels = [ln.split(":")[0] for ln in t.split("\n")]
    assert labels == [
        "Title", "Company", "Location", "Work model", "Seniority", "Department",
        "Description",
    ]
    assert "Location: Remote, Brazil, São Paulo, BR" in t


def test_deterministic_and_no_salary():
    j = mk()
    assert job_embedding_text(j, "Acme") == job_embedding_text(j, "Acme")
    assert "100" not in job_embedding_text(j, "Acme")
    assert "BRL" not in job_embedding_text(j, "Acme")


def test_description_truncated_at_whitespace():
    t = job_embedding_text(mk(description="word " * 5000), "Acme")
    desc = t.split("Description: ")[1]
    assert len(desc) <= DESCRIPTION_MAX_CHARS
    assert desc.endswith("word")


def test_truncates_unbroken_text_hard():
    t = job_embedding_text(mk(description="x" * 20000), "Acme")
    assert len(t.split("Description: ")[1]) == DESCRIPTION_MAX_CHARS


def test_cleans_html_control_chars_and_whitespace():
    t = job_embedding_text(
        mk(title="A\x00\x07  B", description="<p>Hello\n\n<b>world</b></p>​!"),
        "  Ac\tme ",
    )
    assert "Title: A B" in t and "Company: Ac me" in t
    assert "Description: Hello world !" in t
    assert "<" not in t and "\x00" not in t


def test_skips_empty_fields():
    t = job_embedding_text(
        mk(department=None, city=None, country=None, description=""), "Acme"
    )
    assert "Department" not in t and "Description" not in t
    assert "Location: Remote, Brazil" in t
