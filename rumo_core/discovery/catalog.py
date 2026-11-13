"""Curated catalog of companies known to host public Greenhouse boards."""

import json
from dataclasses import dataclass
from pathlib import Path

from rumo_core.discovery.names import TOKEN_RULE

CATALOG_PATH = Path(__file__).parent / "catalog" / "greenhouse.json"


@dataclass(frozen=True)
class CatalogEntry:
    token: str
    name: str


def load_catalog(path: Path | str | None = None) -> list[CatalogEntry]:
    raw = json.loads(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    entries: list[CatalogEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        token = item.get("token")
        name = item.get("name")
        if not isinstance(token, str) or not TOKEN_RULE.fullmatch(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        entries.append(CatalogEntry(token=token, name=name if isinstance(name, str) and name else token))
    return entries
