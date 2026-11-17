"""Document lines: the unit of a resume or letter.

A line is either a fact copied from the profile by code (origin "fact"), text
written by the model ("ai") or text edited by the person ("user"). Every line
cites the profile items it came from (source_refs) and carries the validator's
reasons (violations). A document with any violation is "needs_fix" and is
never exported.
"""

from typing import Literal

from pydantic import BaseModel

Section = Literal[
    "contact", "summary", "experience", "bullet", "project", "education",
    "skills", "languages", "certifications", "letter",
]


class DocLine(BaseModel):
    id: str
    section: Section
    text: str
    fields: dict[str, str] = {}
    parent: str | None = None
    source_refs: list[str] = []
    origin: Literal["fact", "ai", "user"]
    covers: list[str] = []
    violations: list[str] = []


def document_status(lines: list[DocLine]) -> Literal["valid", "needs_fix"]:
    return "needs_fix" if any(line.violations for line in lines) else "valid"


_TEMPLATE_BY_COUNTRY = {
    "US": "us",
    "DE": "de", "AT": "de", "CH": "de",
    "AU": "au",
    "BR": "br",
}


def template_for(country: str | None) -> str:
    return _TEMPLATE_BY_COUNTRY.get((country or "").strip().upper(), "us")


def locale_for(language: str | None) -> str:
    lang = (language or "").strip().lower()
    return lang if lang in ("pt", "en", "de") else "en"
