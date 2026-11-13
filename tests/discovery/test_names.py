import re

import pytest

from rumo_core.discovery.names import token_variants
from rumo_core.sources.greenhouse import GreenhouseSource

RULE = re.compile(r"[a-z0-9_-]{2,64}")


def test_variants_strip_legal_suffixes_and_accents():
    v = token_variants("Acme Corp.")
    assert "acmecorp" in v and "acme" in v
    assert "cafemunch" in token_variants("Café Münch GmbH")


def test_variants_only_safe_chars():
    for name in ["Foo & Bar, Inc.", "Hello World!", "a_b-c d", "Ünï Cödé Ltda"]:
        for t in token_variants(name):
            assert RULE.fullmatch(t)


@pytest.mark.parametrize(
    "name",
    ["../etc", "a b\u0000c", "日本語 会社", "", "   ", "x" * 5000, "Inc", "\n\t", "a/../../b", "‮ evil"],
)
def test_variants_hostile_inputs_yield_nothing_unsafe(name):
    out = token_variants(name)
    assert len(out) <= 3
    for t in out:
        assert RULE.fullmatch(t)
        assert ".." not in t and "/" not in t


def test_variants_too_long_name_is_empty():
    assert token_variants("a" * 201) == []
    assert token_variants("x" * 5000) == []


def test_variants_max_three_unique():
    v = token_variants("Acme Corp.")
    assert len(v) <= 3 and len(set(v)) == len(v)
    assert v == ["acmecorp", "acme", "acme-corp"]


def test_variants_accepted_by_greenhouse_connector():
    gh = GreenhouseSource.__new__(GreenhouseSource)
    for t in token_variants("Café Münch GmbH") + token_variants("Foo Bar Inc"):
        assert gh.normalize_identifier(t) == t
