"""Shared helpers for building LLM prompts from untrusted text."""

import re


def neutralise(tag: str, text: str) -> str:
    """Break any opening/closing `tag` lookalike (case/whitespace-insensitive) in `text`."""

    def _sub(m: re.Match[str]) -> str:
        return (r"<\/" if m.group(1) else "<\\") + tag

    return re.sub(rf"<\s*(/?)\s*{re.escape(tag)}", _sub, text, flags=re.IGNORECASE)


def wrap_untrusted(tag: str, text: str, also: tuple[str, ...] = ()) -> str:
    """Wrap `text` in <tag>...</tag>, neutralising `tag` (and any `also` tags) inside."""
    for t in (tag, *also):
        text = neutralise(t, text)
    return f"<{tag}>\n{text}\n</{tag}>"
