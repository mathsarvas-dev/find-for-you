"""Gaps: a requirement the resume does not cover, answered by the person.

"Tenho isso" adds the answer to the profile (a skill when it reads like a tool
name, otherwise a note), as a new profile version. "Não tenho" only records
the gap; the profile does not change. Pure: the DB work is in the API.
"""

import re
from typing import Literal

from rumo_core.matching.ai_score import _fold
from rumo_core.profile.schema import CanonicalProfile, Note, Skill

MAX_GAP_ANSWER = 200
_NOTE_TOPIC_CHARS = 120
_SENTENCE_MARKS = re.compile(r"[.!?,;:]")


def classify_gap_answer(text: str) -> Literal["skill", "note"]:
    """A short answer without sentence punctuation is a skill (a tool name);
    anything longer, or with a full stop, comma or question mark, is a note."""
    t = text.strip()
    if len(t) <= 40 and len(t.split()) <= 4 and not _SENTENCE_MARKS.search(t):
        return "skill"
    return "note"


def add_gap_to_profile(profile: CanonicalProfile, requirement_text: str, answer: str) -> CanonicalProfile:
    out = profile.model_copy(deep=True)
    value = answer.strip()
    if classify_gap_answer(value) == "skill":
        key = _fold(value)
        if not any(_fold(s.name.strip()) == key for s in out.skills):
            out.skills.append(Skill(id="", name=value))
    else:
        out.notes.append(Note(id="", topic=requirement_text[:_NOTE_TOPIC_CHARS], text=value))
    return out
