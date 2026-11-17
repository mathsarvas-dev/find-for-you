"""Application questions: parsing, sensitive classification and the rules for drafting.

Sensitive questions (work authorization, visa, salary, disability, gender, race,
criminal record, birth date, relocation, personal data) are never drafted by the
model: the person answers them. The keyword list is versioned and conservative:
when in doubt, a question is treated as sensitive.
"""

import re
from dataclasses import dataclass
from typing import Literal

from rumo_core.matching.ai_score import _fold
from rumo_core.sources.base import RawQuestion

SENSITIVE_KEYWORDS_VERSION = 1
MAX_PASTED_QUESTIONS = 30
MAX_PASTED_CHARS = 1000

# Folded (accent-less, lowercase). A keyword of 4 letters or fewer matches a whole word;
# a longer one matches at the start of a word (so "sponsor" matches "sponsorship").
SENSITIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "work_authorization": (
        "authorized to work", "eligible to work", "right to work", "work permit",
        "sponsorship", "sponsor", "autorizacao de trabalho", "patrocinio", "arbeitserlaubnis",
    ),
    "visa": ("visa", "visto", "visum", "aufenthalt"),
    "salary": (
        "salary", "salario", "pretensao", "remuneracao", "compensation", "expected pay",
        "gehalt", "ctc",
    ),
    "disability": ("disab", "deficien", "behinderung"),
    "gender": ("gender", "genero", "sexo", "geschlecht", "pronoun", "pronome"),
    "race": ("race", "raca", "ethnic", "etnia", "hispanic", "latino"),
    "criminal": ("criminal", "antecedente", "convict", "felony", "vorstrafe", "fuhrungszeugnis"),
    "birth": ("date of birth", "birth", "nascimento", "idade", "age", "geburt"),
    "relocation": ("relocat", "mudanca", "realoca", "umzug", "willing to move"),
    "veteran": ("veteran", "military", "militar"),
    "orientation": ("sexual orientation", "orientacao sexual"),
    "personal": (
        "first name", "last name", "full name", "nome", "name", "e-mail", "email", "phone",
        "telefone", "telefon", "address", "endereco", "adresse", "linkedin",
    ),
}

# answer options that mean "I prefer not to say": the question is about identity
_DECLINE_PHRASES = (
    "decline to self-identify", "prefer not to say", "prefer not to answer",
    "prefiro nao dizer", "nao desejo informar",
)

# sensitive category -> the reusable answer-bank key that may be suggested for it
SENSITIVE_BANK_KEYS: dict[str, str] = {
    "salary": "salary_expectation",
    "work_authorization": "work_authorization",
    "visa": "work_authorization",
    "relocation": "relocation",
}

_ALWAYS_SENSITIVE_SECTIONS = ("compliance", "demographic")
_BLANK_LINE = re.compile(r"\n\s*\n")


def _matches(folded: str, keyword: str) -> bool:
    if len(keyword) <= 4 and " " not in keyword:
        return re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", folded) is not None
    return re.search(r"(?<!\w)" + re.escape(keyword), folded) is not None


def sensitive_category(label: str, options: list[str] | None = None) -> str | None:
    folded = _fold(label).casefold()
    for category, keywords in SENSITIVE_KEYWORDS.items():
        if any(_matches(folded, k) for k in keywords):
            return category
    if options:
        for option in options:
            opt = _fold(option).casefold()
            if any(phrase in opt for phrase in _DECLINE_PHRASES):
                return "demographic"
    return None


def is_sensitive(label: str, options: list[str] | None = None, section: str | None = None) -> bool:
    if section in _ALWAYS_SENSITIVE_SECTIONS:
        return True
    return sensitive_category(label, options) is not None


@dataclass(frozen=True)
class ParsedQuestion:
    label: str
    kind: Literal["text", "choice", "bool", "file"]
    options: list[str] | None
    required: bool
    sensitive: bool


def map_greenhouse(raw: list[RawQuestion]) -> list[ParsedQuestion]:
    out: list[ParsedQuestion] = []
    for q in raw:
        if q.field_type == "input_hidden" or not q.label.strip():
            continue
        options: list[str] | None = None
        if q.field_type in ("input_text", "textarea"):
            kind: Literal["text", "choice", "bool", "file"] = "text"
        elif q.field_type == "input_file":
            kind = "file"
        elif q.field_type == "multi_value_single_select" and q.values and {
            _fold(v).strip() for v in q.values
        } <= {"yes", "no"}:
            kind = "bool"
        else:
            kind = "choice"
            options = list(q.values) or None
        out.append(ParsedQuestion(
            label=q.label.strip(),
            kind=kind,
            options=options,
            required=q.required,
            sensitive=is_sensitive(q.label, q.values, q.section),
        ))
    return out


def parse_pasted(text: str) -> list[str]:
    """One question per block separated by a blank line; the lines of a block are joined."""
    out: list[str] = []
    for block in _BLANK_LINE.split(text.replace("\r\n", "\n")):
        question = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if not question:
            continue
        out.append(question[:MAX_PASTED_CHARS])
        if len(out) == MAX_PASTED_QUESTIONS:
            break
    return out
