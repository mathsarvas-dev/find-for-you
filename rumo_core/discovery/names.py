"""Pure helpers turning company names into candidate board tokens."""

import re
import unicodedata

TOKEN_RULE = re.compile(r"[a-z0-9_-]{2,64}")
MAX_NAME_LEN = 200
MAX_VARIANTS = 3
LEGAL_SUFFIXES = frozenset(
    {"inc", "llc", "ltd", "gmbh", "ag", "sa", "ltda", "corp", "co", "plc"}
)
_WORD = re.compile(r"[a-z0-9]+")
# "S.A.", "S/A", "S. A" -> "sa" (Brazilian legal suffix), before splitting on punctuation
_SA = re.compile(r"(?<![a-z0-9])s\s?[./]\s?a(?![a-z0-9])")


def employer_key(name: str | None) -> str | None:
    """Comparable key for an employer: lowercase words without legal suffixes.

    None when the name is empty or only legal suffixes: such employers are
    never matched with another company (no cross-company dedupe).
    """
    if not isinstance(name, str) or not name or len(name) > MAX_NAME_LEN:
        return None
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    folded = _SA.sub("sa", folded)
    core = _WORD.findall(folded)
    while core and core[-1] in LEGAL_SUFFIXES:
        core.pop()
    return "".join(core) or None


def token_variants(name: str) -> list[str]:
    if not isinstance(name, str) or not name or len(name) > MAX_NAME_LEN:
        return []
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    folded = _SA.sub("sa", folded)
    words = _WORD.findall(folded)
    core = list(words)
    while core and core[-1] in LEGAL_SUFFIXES:
        core.pop()
    if not core:
        return []
    candidates = ["".join(words), "".join(core), "-".join(words)]
    out: list[str] = []
    for cand in candidates:
        if TOKEN_RULE.fullmatch(cand) and cand not in out:
            out.append(cand)
    return out[:MAX_VARIANTS]
