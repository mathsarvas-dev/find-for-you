"""Shared helpers for building LLM prompts from untrusted text."""

import re


def wrap_untrusted(tag: str, text: str) -> str:
    """Wrap `text` in <tag>...</tag>, neutralising any closing-tag lookalike inside."""
    safe = re.sub(rf"</{re.escape(tag)}", rf"<\\/{tag}", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{safe}\n</{tag}>"
