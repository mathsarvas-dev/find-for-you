import io

import pytest
from docx import Document
from pypdf import PdfWriter

from rumo_core.profile.text_extract import TextExtractError, extract_text


def make_pdf(text: str | None) -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    if text is not None:
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page = w.pages[0]
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def make_docx() -> bytes:
    d = Document()
    d.add_paragraph("Ana   Silva")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Python"
    t.cell(0, 1).text = "Senior"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_extract_pdf_text():
    out = extract_text(make_pdf("Hello   World"), "application/pdf", "cv.pdf")
    assert out == "Hello World"


def test_extract_docx_text():
    out = extract_text(make_docx(), "application/octet-stream", "cv.docx")
    assert "Ana Silva" in out and "Python" in out and "Senior" in out


def test_type_by_content_not_extension():
    assert "Hello" in extract_text(make_pdf("Hello"), "text/plain", "cv.txt")


def test_scanned_pdf_raises():
    with pytest.raises(TextExtractError, match="texto"):
        extract_text(make_pdf(None), "application/pdf", "scan.pdf")


def test_unsupported_type_raises():
    with pytest.raises(TextExtractError):
        extract_text(b"just some text", "text/plain", "cv.txt")


def test_corrupt_file_raises():
    with pytest.raises(TextExtractError):
        extract_text(b"%PDF-1.4 garbage", "application/pdf", "cv.pdf")
    with pytest.raises(TextExtractError):
        extract_text(b"PK\x03\x04garbage", "application/zip", "cv.docx")
