"""Deterministic detection of gaps in a canonical profile (no DB, no LLM)."""

import re
from dataclasses import dataclass

from rumo_core.profile.schema import CanonicalProfile, Experience

PREFERENCE_KEYS = ("salary_expectation", "work_authorization", "availability", "relocation")

PREFERENCE_LABELS = {
    "salary_expectation": "pretensão salarial",
    "work_authorization": "autorização de trabalho / visto",
    "availability": "disponibilidade para começar",
    "relocation": "disposição para mudança de cidade",
}

_DIGIT = re.compile(r"\d")

_EXP_BASE = 10
_EXP_STEP = 3
_PRIO_SUMMARY = 100
_PRIO_TARGET_ROLE = 101
_PRIO_SKILLS = 102
_PRIO_PREF_BASE = 200


@dataclass(frozen=True)
class Gap:
    id: str
    kind: str
    target_ref: str | None
    hint: str
    priority: int


def _gap(kind: str, target_ref: str | None, hint: str, priority: int) -> Gap:
    return Gap(
        id=f"{kind}:{target_ref or '-'}",
        kind=kind,
        target_ref=target_ref,
        hint=hint,
        priority=priority,
    )


def _label(exp: Experience) -> str:
    title, company = exp.title.strip(), exp.company.strip()
    if title and company:
        return f"'{title} @ {company}'"
    if title or company:
        return f"'{title or company}'"
    return "sem título"


def _experience_gaps(exp: Experience, index: int) -> list[Gap]:
    base = _EXP_BASE + index * _EXP_STEP
    label = _label(exp)
    gaps: list[Gap] = []
    achievements = [a for a in exp.achievements if a.text.strip()]
    has_description = bool(exp.description.strip())
    if achievements:
        if not any(_DIGIT.search(a.text) for a in achievements):
            gaps.append(_gap(
                "achievement_without_number", exp.id,
                f"a experiência {label} não tem nenhuma conquista com número", base,
            ))
    elif has_description:
        gaps.append(_gap(
            "achievement_without_number", exp.id,
            f"a experiência {label} não tem nenhuma conquista com número", base,
        ))
    else:
        gaps.append(_gap(
            "experience_without_description", exp.id,
            f"a experiência {label} não tem descrição nem conquistas", base,
        ))
    if not exp.start.strip():
        gaps.append(_gap(
            "experience_missing_dates", exp.id,
            f"a experiência {label} não tem data de início", base + 1,
        ))
    return gaps


def detect_gaps(
    profile: CanonicalProfile, answered_preferences: frozenset[str] = frozenset()
) -> list[Gap]:
    gaps: list[Gap] = []
    for index, exp in enumerate(profile.experiences):
        if not exp.id:
            continue
        gaps.extend(_experience_gaps(exp, index))
    if not profile.summary.strip():
        gaps.append(_gap("missing_summary", None, "o perfil não tem resumo profissional", _PRIO_SUMMARY))
    if not profile.basics.headline.strip():
        gaps.append(_gap(
            "missing_target_role", None, "o perfil não tem cargo-alvo (headline)", _PRIO_TARGET_ROLE,
        ))
    if not any(s.name.strip() for s in profile.skills):
        gaps.append(_gap("missing_skills", None, "o perfil não tem habilidades listadas", _PRIO_SKILLS))
    for i, key in enumerate(PREFERENCE_KEYS):
        if key not in answered_preferences:
            gaps.append(_gap(
                f"pref_{key}", None, f"preferência ainda não informada: {PREFERENCE_LABELS[key]}", _PRIO_PREF_BASE + i,
            ))
    return sorted(gaps, key=lambda g: (g.priority, g.id))
