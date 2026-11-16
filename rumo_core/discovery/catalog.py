"""Curated catalog of companies known to host public boards, one file per ATS.

`catalog/<source>.json` lists `{"token", "name", "country"?}` entries. Only tokens
confirmed by a real answer of the board belong here (populating the lists is a
backlog item). A source with no file, or an empty one, simply has no catalog.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from rumo_core.discovery.names import TOKEN_RULE

CATALOG_DIR = Path(__file__).parent / "catalog"
CATALOG_PATH = CATALOG_DIR / "greenhouse.json"  # kept for callers that name the default file
_COUNTRY = re.compile(r"[A-Za-z]{2}")


@dataclass(frozen=True)
class CatalogEntry:
    token: str
    name: str
    country: str | None = None


def load_catalog(source: str = "greenhouse", path: Path | str | None = None) -> list[CatalogEntry]:
    target = Path(path) if path is not None else CATALOG_DIR / f"{source}.json"
    if not target.exists():
        return []
    raw = json.loads(target.read_text(encoding="utf-8"))
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
        country = item.get("country")
        country = country.upper() if isinstance(country, str) and _COUNTRY.fullmatch(country) else None
        entries.append(CatalogEntry(
            token=token,
            name=name if isinstance(name, str) and name else token,
            country=country,
        ))
    return entries
