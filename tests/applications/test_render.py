import io
import sys
from datetime import date

import pypdf
import pytest

from rumo_core.applications.lines import DocLine
from rumo_core.applications.pdf import PdfUnavailable
from rumo_core.applications.render import (
    render_html,
    render_letter_html,
    render_letter_text,
    render_pdf,
    render_text,
)

HOSTILE = "<script>alert(1)</script> Airflow"


def _lines(bullet_text="Reduzi o tempo do pipeline em 40% com Airflow"):
    return [
        DocLine(id="L1", section="contact", text="Ana Souza", fields={"kind": "name"}, origin="fact"),
        DocLine(id="L2", section="contact", text="ana@pessoal.example", fields={"kind": "email"}, origin="fact"),
        DocLine(id="L3", section="summary", text="Engenheira de dados com foco em pipelines.", origin="ai",
                source_refs=["summary"]),
        DocLine(id="L4", section="experience", text="Engenheira de Dados — Acme",
                fields={"title": "Engenheira de Dados", "company": "Acme", "start": "2019-03", "end": "atual"},
                origin="fact", source_refs=["exp1"]),
        DocLine(id="L5", section="bullet", text=bullet_text, parent="exp1", origin="ai",
                source_refs=["ach1"]),
        DocLine(id="L6", section="education", text="Bacharelado em Computação — USP",
                fields={"start": "2015", "end": "2019"}, origin="fact", source_refs=["edu1"]),
        DocLine(id="L7", section="skills", text="Python, SQL", origin="fact", source_refs=["sk1", "sk2"]),
        DocLine(id="L8", section="languages", text="Inglês (fluente)", origin="fact", source_refs=["lg1"]),
        DocLine(id="L9", section="certifications", text="AWS SAA — Amazon (2022)", origin="fact",
                source_refs=["ce1"]),
    ]


def _pos(html: str, *needles: str) -> list[int]:
    return [html.index(n) for n in needles]


def test_us_section_order():
    html = render_html(_lines(), template="us", locale="pt")
    positions = _pos(html, "Resumo", "Experiência", "Formação", "Habilidades", "Idiomas", "Certificações")
    assert positions == sorted(positions)


def test_de_is_tabular_with_dates_column():
    de = render_html(_lines(), template="de", locale="de")
    assert '<table class="cv-table"' in de
    assert "Berufserfahrung" in de and "Ausbildung" in de
    us = render_html(_lines(), template="us", locale="pt")
    assert '<table class="cv-table"' not in us


def test_au_referees_only_when_typed():
    without = render_html(_lines(), template="au", locale="en")
    assert "Referees" not in without
    with_refs = render_html(_lines(), template="au", locale="en", referees="Dra. Lee, Acme, lee@example.test")
    assert "Referees" in with_refs
    positions = _pos(with_refs, "Skills", "Experience", "Referees")
    assert positions == sorted(positions)


def test_br_headings_in_portuguese():
    html = render_html(_lines(), template="br", locale="pt")
    assert "Experiência" in html and "Formação" in html and "Experience" not in html


def test_headings_follow_locale_not_template():
    html = render_html(_lines(), template="de", locale="en")
    assert "Experience" in html and "cv-table" in html


def test_no_img_or_photo_in_any_template():
    for template in ("us", "de", "au", "br"):
        html = render_html(_lines(), template=template, locale="en", referees="x")
        body = html.split("</style>")[-1].lower()  # the markup, not the stylesheet
        assert "<img" not in body
        assert "photo" not in body and "foto" not in body
    letter = render_letter_html(_lines(), template="us", locale="en", job_title="Data Engineer",
                                company_name="Globex", sender_lines=_lines()[:2], today=date(2026, 11, 16))
    assert "<img" not in letter.lower()


def test_html_escapes_hostile_text():
    html = render_html(_lines(bullet_text=HOSTILE), template="us", locale="pt")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_text_version_has_same_lines():
    text = render_text(_lines(), locale="pt", referees="Dra. Lee")
    assert "Ana Souza" in text and "Reduzi o tempo do pipeline em 40% com Airflow" in text
    assert "- Engenheira de Dados — Acme" in text
    assert "  - Reduzi o tempo" in text
    assert "EXPERIÊNCIA" in text
    # plain text: markup is shown literally, never interpreted
    assert "<script>alert(1)</script>" in render_text(_lines(bullet_text=HOSTILE), locale="pt")


def test_letter_salutation_and_anschreiben_by_locale():
    kwargs = dict(template="de", job_title="Dateningenieurin", company_name="Siemens",
                  sender_lines=_lines()[:2], today=date(2026, 11, 16))
    de = render_letter_html([DocLine(id="L1", section="letter", text="Ich bewerbe mich.", origin="ai")],
                            locale="de", **kwargs)
    assert "Anschreiben" in de and "Sehr geehrte Damen und Herren," in de
    assert "Bewerbung als Dateningenieurin" in de and "Mit freundlichen Grüßen" in de
    en = render_letter_text([DocLine(id="L1", section="letter", text="I apply.", origin="ai")],
                            locale="en", job_title="Data Engineer", company_name="Globex",
                            sender_lines=_lines()[:2], today=date(2026, 11, 16))
    assert "Dear Hiring Team," in en and "Application for Data Engineer" in en


def test_pdf_unavailable_is_a_clear_error(monkeypatch):
    import types

    def boom(*args, **kwargs):
        raise OSError("libpango missing")

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=boom))
    with pytest.raises(PdfUnavailable):
        render_pdf("<p>x</p>")


@pytest.mark.slow
@pytest.mark.parametrize("template", ["us", "de", "au", "br"])
def test_pdf_each_template_starts_with_pdf_and_keeps_accents(template):
    try:
        pdf = render_pdf(render_html(_lines(), template=template, locale="pt", referees="Dra. Lee"))
    except PdfUnavailable as exc:
        if sys.platform == "win32":
            pytest.skip(f"WeasyPrint native libs missing on host Windows: {exc}")
        raise
    assert pdf[:4] == b"%PDF"
    text = " ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    assert "Engenheira" in text
