"""Compliance gate for portals and feeds.

`policy.json` is versioned with the code and carries the verdict of each
site (checked_at, evidence). The verdict supports the user's decision; it is
not legal advice (the UI says so). `check` combines the verdict with the
user's own choice (`PortalUserState`) and with the manual import path.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

POLICY_PATH = Path(__file__).with_name("policy.json")
STATUSES = frozenset({"allowed", "conditional", "blocked"})
KINDS = frozenset({"portal", "feed"})
_REQUIRED = (
    "id", "name", "kind", "status", "checked_at", "evidence", "hosts",
    "paths_allowed", "min_interval_seconds", "daily_cap",
)

MSG_BLOCKED = "Este portal proíbe coleta automática. Use “Importar por link”."
MSG_CONDITIONAL_OFF = (
    "Portal desligado: os termos de uso não foram totalmente verificados. "
    "Ligue na aba Portais se aceitar o risco."
)
MSG_BLOCKED_BY_SITE = "O site recusou nosso acesso; a coleta foi suspensa."
MSG_PATH_NOT_ALLOWED = "Este caminho não está liberado para coleta automática neste portal."
MSG_UNKNOWN = "Portal desconhecido."


@dataclass(frozen=True)
class PortalPolicy:
    id: str
    name: str
    kind: Literal["portal", "feed"]
    status: Literal["allowed", "conditional", "blocked"]
    checked_at: date
    evidence_urls: tuple[str, ...]
    evidence_summary: str
    hosts: tuple[str, ...]
    paths_allowed: tuple[str, ...]
    min_interval_seconds: float
    daily_cap: int


@dataclass(frozen=True)
class PortalUserState:
    enabled: bool = False
    blocked_by_site: bool = False


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: Literal[
        "allowed", "blocked", "conditional_off", "blocked_by_site",
        "path_not_allowed", "unknown_portal",
    ]
    message: str


_CACHE: dict[str, dict[str, PortalPolicy]] = {}


def _parse_entry(entry) -> PortalPolicy:
    if not isinstance(entry, dict):
        raise ValueError("entrada de política inválida")
    missing = [k for k in _REQUIRED if k not in entry]
    if missing:
        raise ValueError(f"política sem campos: {missing}")
    if entry["status"] not in STATUSES:
        raise ValueError(f"status inválido: {entry['status']!r}")
    if entry["kind"] not in KINDS:
        raise ValueError(f"tipo inválido: {entry['kind']!r}")
    evidence = entry["evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("evidência inválida")
    return PortalPolicy(
        id=str(entry["id"]),
        name=str(entry["name"]),
        kind=entry["kind"],
        status=entry["status"],
        checked_at=date.fromisoformat(entry["checked_at"]),
        evidence_urls=tuple(str(u) for u in evidence.get("urls", [])),
        evidence_summary=str(evidence.get("summary", "")),
        hosts=tuple(str(h).lower() for h in entry["hosts"]),
        paths_allowed=tuple(str(p) for p in entry["paths_allowed"]),
        min_interval_seconds=float(entry["min_interval_seconds"]),
        daily_cap=int(entry["daily_cap"]),
    )


def load_policy(path: Path | str | None = None) -> dict[str, PortalPolicy]:
    key = str(path or POLICY_PATH)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with open(key, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("a política deve ser uma lista")
    policy: dict[str, PortalPolicy] = {}
    for entry in data:
        item = _parse_entry(entry)
        if item.id in policy:
            raise ValueError(f"id repetido na política: {item.id!r}")
        policy[item.id] = item
    _CACHE[key] = policy
    return policy


def portal_for_url(url: str, policy: dict[str, PortalPolicy] | None = None) -> PortalPolicy | None:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for item in (policy if policy is not None else load_policy()).values():
        for h in item.hosts:
            if host == h or host.endswith("." + h):
                return item
    return None


def check(
    portal: str,
    url: str | None = None,
    *,
    state: PortalUserState | None = None,
    manual: bool = False,
    policy: dict[str, PortalPolicy] | None = None,
) -> Verdict:
    rules = policy if policy is not None else load_policy()
    item = rules.get(portal)
    if item is None:
        return Verdict(False, "unknown_portal", MSG_UNKNOWN)
    if manual:
        return Verdict(True, "allowed", "")
    st = state or PortalUserState()
    if item.status == "blocked":
        return Verdict(False, "blocked", MSG_BLOCKED)
    if item.status == "conditional" and not st.enabled:
        return Verdict(False, "conditional_off", MSG_CONDITIONAL_OFF)
    if st.blocked_by_site:
        return Verdict(False, "blocked_by_site", MSG_BLOCKED_BY_SITE)
    if item.kind == "portal" and url is not None:
        path = urlsplit(url).path or "/"
        if not any(path.startswith(p) for p in item.paths_allowed):
            return Verdict(False, "path_not_allowed", MSG_PATH_NOT_ALLOWED)
    return Verdict(True, "allowed", "")
