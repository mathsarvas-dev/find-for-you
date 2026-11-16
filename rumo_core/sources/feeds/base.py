"""Aggregator feeds: parameters, items, attribution and the feed protocol.

A feed is a public listing API of a job aggregator. Its jobs hang off synthetic
companies (one per employer name) and carry the aggregator's attribution, which
the UI must show. Feeds run only behind the compliance gate (`policy.json`) and
through `SafeClient` (same limits and robots rules as every other source).
"""

from dataclasses import dataclass
from typing import Protocol

from rumo_core.sources.base import RawJob
from rumo_core.sources.country import norm_country

MAX_KEYWORDS = 5
MAX_KEYWORD_LEN = 60
_PARAM_KEYS = frozenset({"keywords", "country"})


@dataclass(frozen=True)
class Attribution:
    label: str
    url: str


@dataclass(frozen=True)
class FeedParams:
    keywords: tuple[str, ...] = ()
    country: str | None = None

    @classmethod
    def from_dict(cls, data) -> "FeedParams":
        if not isinstance(data, dict):
            raise ValueError("parâmetros do feed devem ser um objeto")
        unknown = set(data) - _PARAM_KEYS
        if unknown:
            raise ValueError(f"parâmetros desconhecidos: {sorted(unknown)}")
        raw_keywords = data.get("keywords") or []
        if not isinstance(raw_keywords, list) or not all(isinstance(k, str) for k in raw_keywords):
            raise ValueError("palavras-chave devem ser uma lista de textos")
        keywords: list[str] = []
        seen: set[str] = set()
        for keyword in raw_keywords:
            text = keyword.strip()
            if not 1 <= len(text) <= MAX_KEYWORD_LEN:
                raise ValueError("cada palavra-chave deve ter de 1 a 60 caracteres")
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            keywords.append(text)
        if len(keywords) > MAX_KEYWORDS:
            raise ValueError("use no máximo 5 palavras-chave")
        country = data.get("country")
        if country is not None and not isinstance(country, str):
            raise ValueError("país inválido")
        return cls(tuple(keywords), norm_country(country))

    def to_dict(self) -> dict:
        return {"keywords": list(self.keywords), "country": self.country}


@dataclass(frozen=True)
class FeedItem:
    raw: RawJob
    employer_name: str | None
    employer_url: str | None = None


class FeedSource(Protocol):
    name: str
    needs_key: bool
    attribution: Attribution

    def key_configured(self, settings) -> bool: ...

    async def fetch(
        self, params: FeedParams, *, known_ids: frozenset[str] = frozenset(),
    ) -> list[FeedItem]: ...

    async def aclose(self) -> None: ...


def policy_limits(name: str) -> dict:
    """`min_interval` / `daily_cap` of the feed's policy entry, for `SafeClient.get`."""
    from rumo_core.sources.portals.gate import load_policy

    entry = load_policy().get(name)
    if entry is None:
        return {}
    return {"min_interval": entry.min_interval_seconds, "daily_cap": entry.daily_cap}
