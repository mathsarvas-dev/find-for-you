"""Resume and letter output: HTML per country template, plain text, and PDF.

Every value is text: Jinja2 autoescapes the HTML, and the plain-text version
never interprets markup. There is no photo and no image in any template.
The PDF comes from HTML through WeasyPrint (pdf.py); nothing binary is stored.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from rumo_core.applications.lines import DocLine
from rumo_core.applications.pdf import html_to_pdf

_HERE = Path(__file__).parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"

HEADINGS: dict[str, dict[str, str]] = {
    "pt": {
        "summary": "Resumo", "experience": "Experiência", "projects": "Projetos",
        "education": "Formação", "skills": "Habilidades", "languages": "Idiomas",
        "certifications": "Certificações", "referees": "Referências",
    },
    "en": {
        "summary": "Summary", "experience": "Experience", "projects": "Projects",
        "education": "Education", "skills": "Skills", "languages": "Languages",
        "certifications": "Certifications", "referees": "Referees",
    },
    "de": {
        "summary": "Profil", "experience": "Berufserfahrung", "projects": "Projekte",
        "education": "Ausbildung", "skills": "Kenntnisse", "languages": "Sprachen",
        "certifications": "Zertifikate", "referees": "Persönliche Daten",
    },
}

SECTION_ORDER: dict[str, tuple[str, ...]] = {
    "us": ("contact", "summary", "experience", "projects", "education", "skills",
           "languages", "certifications"),
    "de": ("contact", "summary", "experience", "education", "skills", "languages",
           "certifications", "projects"),
    "au": ("contact", "summary", "skills", "experience", "education", "certifications",
           "projects", "languages", "referees"),
    "br": ("contact", "summary", "experience", "education", "skills", "languages",
           "certifications", "projects"),
}

# tables with the dates in the first column (German CV layout)
_TABULAR = {"de"}

_LETTER_TEXT = {
    "de": {"kind": "Anschreiben", "subject": "Bewerbung als {title}",
           "salutation": "Sehr geehrte Damen und Herren,", "closing": "Mit freundlichen Grüßen"},
    "en": {"kind": "Cover letter", "subject": "Application for {title}",
           "salutation": "Dear Hiring Team,", "closing": "Sincerely,"},
    "pt": {"kind": "Carta de apresentação", "subject": "Candidatura para {title}",
           "salutation": "Prezada equipe,", "closing": "Atenciosamente,"},
}

_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)


def _css(template: str) -> str:
    base = (_STATIC / "base.css").read_text(encoding="utf-8")
    own = (_STATIC / f"{template}.css").read_text(encoding="utf-8")
    return base + "\n" + own


def _dates(fields: dict[str, str]) -> str:
    start, end = fields.get("start", "").strip(), fields.get("end", "").strip()
    if not start and not end:
        return ""
    return f"{start or '?'} – {end or '?'}"


@dataclass
class _Data:
    name: str
    contact: list[str]
    summary: str
    experiences: list[dict]
    projects: list[dict]
    education: list[dict]
    skills: str
    languages: list[str]
    certifications: list[str]
    referees: str


def _bullets_for(lines: list[DocLine], key: str) -> list[str]:
    return [l.text for l in lines if l.section == "bullet" and l.parent == key]


def _build(lines: list[DocLine], referees: str | None) -> _Data:
    contact = [l for l in lines if l.section == "contact"]
    name = next((c.text for c in contact if c.fields.get("kind") == "name"), "")
    experiences, projects, education = [], [], []
    for l in lines:
        key = l.source_refs[0] if l.source_refs else ""
        if l.section == "experience":
            experiences.append({"header": l.text, "dates": _dates(l.fields),
                                "bullets": _bullets_for(lines, key)})
        elif l.section == "project":
            projects.append({"header": l.text, "dates": "", "bullets": _bullets_for(lines, key)})
        elif l.section == "education":
            education.append({"header": l.text, "dates": _dates(l.fields), "bullets": []})
    return _Data(
        name=name,
        contact=[c.text for c in contact if c.fields.get("kind") != "name"],
        summary=next((l.text for l in lines if l.section == "summary"), ""),
        experiences=experiences,
        projects=projects,
        education=education,
        skills=next((l.text for l in lines if l.section == "skills"), ""),
        languages=[l.text for l in lines if l.section == "languages"],
        certifications=[l.text for l in lines if l.section == "certifications"],
        referees=(referees or "").strip(),
    )


def _order(template: str, referees: str | None) -> tuple[str, ...]:
    order = SECTION_ORDER.get(template, SECTION_ORDER["us"])
    if not (referees or "").strip():
        order = tuple(s for s in order if s != "referees")
    return order


def render_html(lines: list[DocLine], *, template: str, locale: str,
                referees: str | None = None) -> str:
    template = template if template in SECTION_ORDER else "us"
    headings = HEADINGS.get(locale, HEADINGS["en"])
    data = _build(lines, referees)
    tpl = _env.get_template(f"{template}.html")
    return tpl.render(
        locale=locale if locale in HEADINGS else "en",
        template=template,
        css=_css(template),
        H=headings,
        d=data,
        order=_order(template, referees),
        tabular=template in _TABULAR,
    )


def render_text(lines: list[DocLine], *, locale: str, referees: str | None = None,
                template: str = "us") -> str:
    headings = HEADINGS.get(locale, HEADINGS["en"])
    data = _build(lines, referees)
    out: list[str] = []
    if data.name:
        out.append(data.name)
    if data.contact:
        out.append(" | ".join(data.contact))
    for section in _order(template, referees):
        if section == "contact":
            continue
        if section == "summary" and data.summary:
            out += ["", headings["summary"].upper(), data.summary]
        elif section == "experience" and data.experiences:
            out += ["", headings["experience"].upper()]
            for e in data.experiences:
                out.append(f"- {e['header']}" + (f" ({e['dates']})" if e["dates"] else ""))
                out += [f"  - {b}" for b in e["bullets"]]
        elif section == "projects" and data.projects:
            out += ["", headings["projects"].upper()]
            for p in data.projects:
                out.append(f"- {p['header']}")
                out += [f"  - {b}" for b in p["bullets"]]
        elif section == "education" and data.education:
            out += ["", headings["education"].upper()]
            out += [f"- {e['header']}" + (f" ({e['dates']})" if e["dates"] else "") for e in data.education]
        elif section == "skills" and data.skills:
            out += ["", headings["skills"].upper(), data.skills]
        elif section == "languages" and data.languages:
            out += ["", headings["languages"].upper()] + [f"- {x}" for x in data.languages]
        elif section == "certifications" and data.certifications:
            out += ["", headings["certifications"].upper()] + [f"- {x}" for x in data.certifications]
        elif section == "referees" and data.referees:
            out += ["", headings["referees"].upper(), data.referees]
    return "\n".join(out).strip() + "\n"


def _letter_strings(locale: str, job_title: str) -> dict[str, str]:
    s = _LETTER_TEXT.get(locale, _LETTER_TEXT["en"])
    return {"kind": s["kind"], "subject": s["subject"].format(title=job_title),
            "salutation": s["salutation"], "closing": s["closing"]}


def render_letter_html(lines: list[DocLine], *, template: str, locale: str, job_title: str,
                       company_name: str, sender_lines: list[DocLine], today: date) -> str:
    template = template if template in SECTION_ORDER else "us"
    sender = _build(sender_lines, None)
    tpl = _env.get_template("letter.html")
    strings = _letter_strings(locale, job_title)
    return tpl.render(
        locale=locale if locale in HEADINGS else "en",
        template=template,
        css=_css(template),
        sender_name=sender.name,
        contact=sender.contact,
        today=today.isoformat(),
        company=company_name,
        paragraphs=[l.text for l in lines if l.section == "letter"],
        **strings,
    )


def render_letter_text(lines: list[DocLine], *, locale: str, job_title: str,
                       company_name: str, sender_lines: list[DocLine], today: date) -> str:
    sender = _build(sender_lines, None)
    strings = _letter_strings(locale, job_title)
    out = [sender.name] if sender.name else []
    if sender.contact:
        out.append(" | ".join(sender.contact))
    out += ["", today.isoformat(), "", strings["subject"], "", strings["salutation"]]
    out += [l.text for l in lines if l.section == "letter"]
    out += ["", strings["closing"], sender.name]
    return "\n".join(out).strip() + "\n"


def render_pdf(html: str) -> bytes:
    """PdfUnavailable propagates to the caller (the API answers 503)."""
    return html_to_pdf(html)
