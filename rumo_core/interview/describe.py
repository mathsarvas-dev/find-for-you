"""Human-readable before/after text for a profile suggestion (pure, no DB)."""

from rumo_core.interview.gaps import PREFERENCE_LABELS
from rumo_core.interview.suggestions import SuggestionDraft
from rumo_core.profile.schema import CanonicalProfile, Experience

EMPTY = "—"
REMOVED = "(item removido)"


def _experience(profile: CanonicalProfile | None, ref: str | None) -> Experience | None:
    if profile is None or not ref:
        return None
    return next((e for e in profile.experiences if e.id == ref), None)


def _dates(start: str, end: str) -> str:
    return f"{start.strip() or EMPTY} – {end.strip() or EMPTY}"


def _skill(name: str, level: str | None) -> str:
    return f"{name} ({level})" if level and level.strip() else name


def describe_suggestion(
    profile: CanonicalProfile | None, payload: SuggestionDraft
) -> tuple[str, str]:
    """(before, after): current value of the targeted field and the proposed one."""
    op = payload.op
    if op == "set_summary":
        return (profile.summary.strip() if profile and profile.summary.strip() else EMPTY,
                payload.text)
    if op == "set_headline":
        current = profile.basics.headline.strip() if profile else ""
        return (current or EMPTY, payload.text)
    if op == "add_skill":
        names = [_skill(s.name, s.level) for s in profile.skills] if profile else []
        return (", ".join(names) or EMPTY, _skill(payload.name, payload.level))
    if op == "set_preference":
        return (EMPTY, f"{PREFERENCE_LABELS.get(payload.key, payload.key)}: {payload.value}")
    if op == "add_note":
        return (EMPTY, payload.text)

    exp = _experience(profile, payload.target_ref)
    if op == "set_experience_dates":
        if exp is None:
            return (REMOVED, _dates(payload.start, payload.end))
        return (_dates(exp.start, exp.end),
                _dates(payload.start.strip() or exp.start, payload.end.strip() or exp.end))
    if exp is None:
        return (REMOVED, payload.text)
    if op == "set_experience_description":
        return (exp.description.strip() or "(sem descrição)", payload.text)
    # append_achievement
    n = len(exp.achievements)
    if n == 0:
        return ("Nenhuma conquista registrada", payload.text)
    last = exp.achievements[-1].text
    return (f"{n} conquista(s); última: {last}", payload.text)
