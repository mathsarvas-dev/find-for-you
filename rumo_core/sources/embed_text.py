"""Plain-text representation of a job used for embedding (deterministic)."""

import re
import unicodedata

DESCRIPTION_MAX_CHARS = 6000
_WS_BOUNDARY_WINDOW = 200
_TAG = re.compile(r"<[^<>]{0,200}>")
_WS = re.compile(r"\s+")


def _clean(value) -> str:
    if not value:
        return ""
    s = _TAG.sub(" ", str(value))
    s = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf") else c for c in s
    )
    return _WS.sub(" ", s).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit].isspace():
        return cut.rstrip()
    idx = cut.rfind(" ")
    if idx >= limit - _WS_BOUNDARY_WINDOW:
        cut = cut[:idx]
    return cut.rstrip()


def job_embedding_text(job, company_name: str) -> str:
    """Fixed-order labelled text: title, company, location, work model,
    seniority, department, description (truncated). Salary is excluded."""
    location = _clean(job.location_text)
    extra = [x for x in (_clean(job.city), _clean(job.country)) if x]
    if extra:
        location = ", ".join(([location] if location else []) + extra)
    fields = [
        ("Title", _clean(job.title)),
        ("Company", _clean(company_name)),
        ("Location", location),
        ("Work model", _clean(job.work_model)),
        ("Seniority", _clean(job.seniority)),
        ("Department", _clean(job.department)),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value]
    description = _truncate(_clean(job.description), DESCRIPTION_MAX_CHARS)
    if description:
        lines.append(f"Description: {description}")
    return "\n".join(lines)
