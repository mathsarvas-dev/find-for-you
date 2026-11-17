"""Text of each profile item, keyed by the id that the rest of the pipeline cites.

Pure. Contacts never appear here: no e-mail, phone, links, project URL or the
person's name. The pseudo ids "summary" and "skills" cover the summary and the
list of skill names (the same reserved refs the chunker uses).
"""

from rumo_core.matching.ai_score import _render
from rumo_core.profile.schema import CanonicalProfile, Experience


def _span(start: str, end: str) -> str:
    start, end = (start or "").strip(), (end or "").strip()
    return f" ({start or '?'} – {end or '?'})" if (start or end) else ""


def _exp_header(e: Experience) -> str:
    head = " na ".join(p.strip() for p in (e.title, e.company) if p and p.strip())
    loc = f" em {e.location.strip()}" if e.location.strip() else ""
    return f"{head or 'Experiência'}{_span(e.start, e.end)}{loc}"


def item_texts(profile: CanonicalProfile) -> dict[str, str]:
    out: dict[str, str] = {}
    out["summary"] = profile.summary.strip()
    out["skills"] = ", ".join(s.name.strip() for s in profile.skills if s.name.strip())

    for e in profile.experiences:
        if not e.id:
            continue
        header = _exp_header(e)
        desc = e.description.strip()
        out[e.id] = f"{header}. {desc}".strip() if desc else header
        for a in e.achievements:
            if a.id and a.text.strip():
                out[a.id] = f"{a.text.strip()} ({header})"

    for ed in profile.education:
        if not ed.id:
            continue
        what = " em ".join(p.strip() for p in (ed.degree, ed.field) if p and p.strip())
        line = " — ".join(p for p in (what, ed.institution.strip()) if p)
        out[ed.id] = f"{line}{_span(ed.start, ed.end)}".strip()

    for s in profile.skills:
        if s.id and s.name.strip():
            lvl = f" ({s.level.strip()})" if (s.level or "").strip() else ""
            out[s.id] = f"{s.name.strip()}{lvl}"

    for lg in profile.languages:
        if lg.id and lg.name.strip():
            lvl = f" ({lg.level.strip()})" if lg.level.strip() else ""
            out[lg.id] = f"{lg.name.strip()}{lvl}"

    for c in profile.certifications:
        if c.id and c.name.strip():
            out[c.id] = " ".join(p.strip() for p in (c.name, c.issuer, c.date) if p.strip())

    for pr in profile.projects:
        if pr.id and pr.name.strip():  # the project URL is a link: never included
            out[pr.id] = " ".join(p.strip() for p in (pr.name, pr.description) if p.strip())

    for n in profile.notes:
        if n.id and n.text.strip():
            topic = f"{n.topic.strip()}: " if n.topic.strip() else ""
            out[n.id] = f"{topic}{n.text.strip()}"

    return out


def full_profile_text(profile: CanonicalProfile) -> str:
    """Whole profile for the validator's 'does the profile say this' check."""
    return _render(profile, list(profile.experiences), extra=True)


def evidence_block(profile: CanonicalProfile, refs: list[str]) -> str:
    """Lines '[id] text' for the selected refs. An experience also lists each
    achievement on its own line, so the model can cite them individually."""
    texts = item_texts(profile)
    experiences = {e.id: e for e in profile.experiences if e.id}
    lines: list[str] = []
    for ref in refs:
        if ref in experiences:
            if texts.get(ref):
                lines.append(f"[{ref}] {texts[ref]}")
            for a in experiences[ref].achievements:
                if a.id and a.text.strip():
                    lines.append(f"[{a.id}] {a.text.strip()}")
        elif texts.get(ref):
            lines.append(f"[{ref}] {texts[ref]}")
    return "\n".join(lines)
