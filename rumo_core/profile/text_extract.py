"""Plain-text extraction from uploaded resumes (PDF and DOCX)."""

import io
import re
import zipfile


class TextExtractError(Exception):
    """Raised when text cannot be extracted; message is user-facing (PT-BR)."""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return " ".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return " ".join(parts)


def extract_text(data: bytes, content_type: str, filename: str) -> str:
    if data[:1024].lstrip().startswith(b"%PDF"):
        reader = _pdf_text
        kind = "PDF"
    elif data[:2] == b"PK":
        reader = _docx_text
        kind = "DOCX"
    else:
        raise TextExtractError("Tipo de arquivo não suportado. Envie um PDF ou DOCX.")
    try:
        text = _normalize(reader(data))
    except Exception as exc:  # any library failure -> user-facing error
        raise TextExtractError(f"Não foi possível ler o arquivo {kind} (corrompido?).") from exc
    if not text:
        raise TextExtractError(
            "Não foi possível extrair texto do arquivo (PDF escaneado ou vazio?). "
            "Envie um arquivo com texto selecionável."
        )
    return text
