import io
import sys

import pypdf
import pytest

from rumo_core.applications.pdf import PdfUnavailable, html_to_pdf


@pytest.fixture
def weasy():
    try:
        html_to_pdf("<p>x</p>")
    except PdfUnavailable as exc:
        if sys.platform == "win32":
            pytest.skip(f"WeasyPrint native libs missing on host Windows: {exc}")
        raise
    return html_to_pdf


@pytest.mark.slow
def test_pdf_smoke_accents_and_eszett(weasy):
    pdf = weasy("<p>Ação Straße Müller São Paulo</p>")
    assert pdf[:4] == b"%PDF"
    text = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Ação" in text
    assert "Straße" in text


def test_pdf_unavailable_wraps_errors(monkeypatch):
    # a stub module: importing the real weasyprint raises OSError on hosts without its libraries
    import types

    def boom(*args, **kwargs):
        raise OSError("libpango-1.0-0 missing")

    stub = types.SimpleNamespace(HTML=boom)
    monkeypatch.setitem(sys.modules, "weasyprint", stub)
    with pytest.raises(PdfUnavailable):
        html_to_pdf("<p>x</p>")
