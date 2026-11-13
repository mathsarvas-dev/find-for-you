import json
import re

from rumo_core.discovery.catalog import CatalogEntry, load_catalog

RULE = re.compile(r"[a-z0-9_-]{2,64}")


def test_catalog_loads_and_has_known_entries():
    entries = load_catalog()
    tokens = {e.token for e in entries}
    assert {"gitlab", "anthropic"} <= tokens
    assert all(isinstance(e, CatalogEntry) and e.name for e in entries)


def test_catalog_rejects_invalid_tokens_and_duplicates(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps([
        {"token": "good", "name": "Good"},
        {"token": "good", "name": "Dup"},
        {"token": "Bad Token", "name": "Bad"},
        {"token": "../x", "name": "Bad"},
        {"token": "a", "name": "Short"},
        {"token": "ok-2", "name": ""},
        {"name": "NoToken"},
        "junk",
    ]))
    entries = load_catalog(p)
    assert [e.token for e in entries] == ["good", "ok-2"]


def test_catalog_all_valid_and_unique():
    entries = load_catalog()
    toks = [e.token for e in entries]
    assert len(toks) == len(set(toks))
    assert all(RULE.fullmatch(t) for t in toks)


def test_catalog_size_reasonable():
    assert len(load_catalog()) >= 100
