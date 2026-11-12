"""Interview suggestions: schema, anti-invention validator and pure application."""

import re
from typing import Literal

from pydantic import BaseModel

from rumo_core.interview.gaps import PREFERENCE_KEYS, Gap
from rumo_core.profile.schema import Achievement, CanonicalProfile, Experience, Note, Skill

SuggestionOp = Literal[
    "append_achievement",
    "set_experience_description",
    "set_experience_dates",
    "set_summary",
    "add_skill",
    "set_preference",
    "add_note",
]

_EXPERIENCE_OPS = ("append_achievement", "set_experience_description", "set_experience_dates")
_EXPERIENCE_GAPS = (
    "achievement_without_number", "experience_without_description", "experience_missing_dates",
)


class SuggestionDraft(BaseModel):
    op: SuggestionOp
    target_ref: str | None = None
    text: str = ""
    start: str = ""
    end: str = ""
    name: str = ""
    level: str | None = None
    key: str = ""
    value: str = ""


class SuggestionTargetMissing(Exception):
    """The experience a suggestion points at does not exist in the profile."""


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_DECIMAL_TAIL = re.compile(r"[.,](\d{1,2})$")


def _normalize(token: str) -> str:
    m = _DECIMAL_TAIL.search(token)
    if m:
        whole = re.sub(r"[.,]", "", token[: m.start()])
        return f"{whole}.{m.group(1)}"
    return re.sub(r"[.,]", "", token)


def _numbers(text: str) -> set[str]:
    return {_normalize(t) for t in _NUMBER.findall(text)}


def numbers_supported(text: str, sources: list[str]) -> bool:
    """Every numeric token in `text` must appear (normalized) in some source."""
    wanted = _numbers(text)
    if not wanted:
        return True
    available: set[str] = set()
    for s in sources:
        available |= _numbers(s)
    return wanted <= available


def _find_experience(profile: CanonicalProfile, ref: str | None) -> Experience | None:
    if not ref:
        return None
    return next((e for e in profile.experiences if e.id == ref), None)


def _allowed_ops(gap: Gap) -> tuple[str, ...]:
    if gap.kind in _EXPERIENCE_GAPS:
        return (*_EXPERIENCE_OPS, "add_note")
    if gap.kind in ("missing_summary", "missing_target_role"):
        return ("set_summary", "add_note")
    if gap.kind == "missing_skills":
        return ("add_skill", "add_note")
    if gap.kind.startswith("pref_"):
        return ("set_preference",)
    return ()


def _item_sources(exp: Experience) -> list[str]:
    return [exp.description, exp.start, exp.end, *[a.text for a in exp.achievements]]


def validate_suggestion(
    draft: SuggestionDraft, *, answer: str, profile: CanonicalProfile, gap: Gap
) -> str | None:
    """Return None if valid, else a short PT-BR reason."""
    if draft.op not in _allowed_ops(gap):
        return "operação não se aplica a esta lacuna"

    sources = [answer]
    checked = ""
    if draft.op in _EXPERIENCE_OPS:
        exp = _find_experience(profile, draft.target_ref)
        if exp is None:
            return "experiência alvo não existe no perfil"
        if draft.target_ref != gap.target_ref:
            return "experiência alvo diferente da lacuna"
        sources += _item_sources(exp)
        if draft.op == "set_experience_dates":
            if not (draft.start.strip() or draft.end.strip()):
                return "datas vazias"
            checked = f"{draft.start} {draft.end}"
        else:
            if not draft.text.strip():
                return "texto vazio"
            checked = draft.text
    elif draft.op in ("set_summary", "add_note"):
        if not draft.text.strip():
            return "texto vazio"
        checked = draft.text
    elif draft.op == "add_skill":
        name = draft.name.strip()
        if not name:
            return "nome da habilidade vazio"
        if name.lower() not in answer.lower():
            return "habilidade não aparece na resposta"
        checked = name
    elif draft.op == "set_preference":
        if draft.key not in PREFERENCE_KEYS:
            return "preferência desconhecida"
        if gap.kind != f"pref_{draft.key}":
            return "preferência diferente da lacuna"
        if not draft.value.strip():
            return "valor vazio"
        checked = draft.value

    if not numbers_supported(checked, sources):
        return "número ou data que não aparece na resposta"
    return None


def apply_suggestion(
    profile: CanonicalProfile, draft: SuggestionDraft, *, answer: str, gap: Gap
) -> CanonicalProfile:
    """Pure: returns a deep copy with the op applied. Callers validate first."""
    out = profile.model_copy(deep=True)
    if draft.op == "set_preference":
        return out
    if draft.op in _EXPERIENCE_OPS:
        exp = _find_experience(out, draft.target_ref)
        if exp is None:
            raise SuggestionTargetMissing(draft.target_ref)
        if draft.op == "append_achievement":
            exp.achievements.append(Achievement(text=draft.text.strip()))
        elif draft.op == "set_experience_description":
            exp.description = draft.text.strip()
        else:
            if draft.start.strip():
                exp.start = draft.start.strip()
            if draft.end.strip():
                exp.end = draft.end.strip()
    elif draft.op == "set_summary":
        out.summary = draft.text.strip()
    elif draft.op == "add_skill":
        out.skills.append(Skill(name=draft.name.strip(), level=draft.level))
    out.notes.append(Note(topic=gap.kind, text=answer.strip()))
    return out
