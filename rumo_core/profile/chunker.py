"""Deterministic profile chunking (one chunk per semantic unit)."""

import hashlib
from dataclasses import dataclass

from rumo_core.profile.schema import CanonicalProfile


@dataclass
class Chunk:
    source_ref: str
    kind: str
    text: str
    content_hash: str


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _join(parts: list[str]) -> str:
    return "\n".join(p.strip() for p in parts if p and p.strip())


def _ref(item, section: str) -> str:
    if not item.id:
        raise ValueError(f"{section} item without id; run assign_ids first")
    return item.id


def build_chunks(profile: CanonicalProfile, narrative: str | None) -> list[Chunk]:
    """Build chunks in a fixed order: summary, experiences, education, skills
    (languages are folded into the skills chunk), projects, certifications.
    Empty chunks are omitted; content_hash is sha256 of the text. Items must
    have ids (run assign_ids first), otherwise ValueError is raised."""
    raw: list[tuple[str, str, str]] = []

    raw.append(("summary", "summary", _join([profile.summary, narrative or ""])))

    for e in profile.experiences:
        period = " - ".join(p for p in (e.start, e.end) if p)
        raw.append((_ref(e, "experience"), "experience", _join([
            " | ".join(p for p in (e.title, e.company) if p),
            period,
            e.location,
            e.description,
            *[f"- {a.text}" for a in e.achievements if a.text.strip()],
        ])))

    for ed in profile.education:
        period = " - ".join(p for p in (ed.start, ed.end) if p)
        raw.append((_ref(ed, "education"), "education", _join([
            ", ".join(p for p in (ed.degree, ed.field) if p),
            ed.institution,
            period,
        ])))

    skill_names = [
        f"{s.name} ({s.level})" if s.level else s.name
        for s in profile.skills if s.name.strip()
    ]
    langs = [
        f"{lang.name} ({lang.level})" if lang.level else lang.name
        for lang in profile.languages if lang.name.strip()
    ]
    raw.append(("skills", "skills", _join([
        "Habilidades: " + ", ".join(skill_names) if skill_names else "",
        "Idiomas: " + ", ".join(langs) if langs else "",
    ])))

    for p in profile.projects:
        raw.append((_ref(p, "project"), "project",
                    _join([p.name, p.description, p.url])))

    for c in profile.certifications:
        raw.append((_ref(c, "certification"), "certification",
                    _join([c.name, c.issuer, c.date])))

    return [Chunk(ref, kind, text, _hash(text)) for ref, kind, text in raw if text]
