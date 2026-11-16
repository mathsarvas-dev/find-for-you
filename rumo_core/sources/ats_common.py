"""Helpers shared by the ATS connectors (JSON/XML APIs and public boards).

Every connector reads its data through `SafeClient` (kind="api"), maps only
the keys its verified fixture shows, and drops items without id, title or URL.
Text from the board is data: it is kept as text and never interpreted.
"""

import json
import re
from datetime import UTC, datetime

from rumo_core.sources.base import SourceBadResponse, SourceNotFound
from rumo_core.sources.http import SafeResponse

_IDENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}")
_WORK_MODELS = frozenset({"remote", "hybrid", "onsite"})


def not_found_or_raise(resp: SafeResponse, name: str) -> None:
    if resp.status in (404, 410):
        raise SourceNotFound(f"{name}: quadro não encontrado")


def json_of(resp: SafeResponse, name: str):
    try:
        return json.loads(resp.body)
    except ValueError as exc:
        raise SourceBadResponse(f"{name} devolveu JSON inválido") from exc


def clean_str(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def clean_id(value) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value).strip()
    return text or None


def work_model(value) -> str | None:
    if isinstance(value, str) and value.strip().lower() in _WORK_MODELS:
        return value.strip().lower()
    return None


def country_code(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Z]{2}", value.strip()):
        return value.strip()
    return None


def from_epoch_ms(value) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def from_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def identifier_slug(text: str, name: str) -> str:
    value = (text or "").strip().lower()
    if not _IDENT.fullmatch(value):
        raise ValueError(f"identificador de {name} inválido")
    return value

