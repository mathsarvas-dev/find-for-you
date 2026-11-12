"""Shared ISO 3166-1 alpha-2 country normaliser."""

import re

_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def norm_country(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not _COUNTRY_RE.match(value):
        raise ValueError("País deve ter 2 letras (ISO 3166-1 alpha-2).")
    return value.upper()
