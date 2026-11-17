"""HTML -> PDF with WeasyPrint. Native libraries are imported lazily so that a
host without Pango/HarfBuzz gets a clear PdfUnavailable instead of a crash."""


class PdfUnavailable(Exception):
    """WeasyPrint or one of its native libraries is not usable here."""


def html_to_pdf(html: str) -> bytes:
    try:
        import weasyprint
    except (ImportError, OSError) as exc:
        raise PdfUnavailable(str(exc)) from exc
    try:
        return weasyprint.HTML(string=html).write_pdf()
    except Exception as exc:  # WeasyPrint raises a variety of errors
        raise PdfUnavailable(str(exc)) from exc
