import math

import pytest

from rumo_core.embeddings.bge_m3 import BgeM3Embedder


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_empty_list_does_not_load_model(monkeypatch):
    emb = BgeM3Embedder()

    def boom(*a, **k):
        raise AssertionError("model must not be loaded")

    monkeypatch.setattr(emb, "_load", boom)
    assert emb.embed([]) == []


@pytest.fixture(scope="module")
def embedder():
    return BgeM3Embedder()


@pytest.mark.slow
def test_dimensions_and_normalization(embedder):
    [v] = embedder.embed(["Engenheiro de software"])
    assert embedder.dimensions == 1024
    assert embedder.model_version == "bge-m3@1"
    assert len(v) == 1024
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, abs_tol=1e-3)


@pytest.mark.slow
def test_empty_list_returns_empty_list(embedder):
    assert embedder.embed([]) == []


@pytest.mark.slow
def test_empty_string_returns_valid_vector(embedder):
    [v] = embedder.embed([""])
    assert len(v) == 1024
    assert all(math.isfinite(x) for x in v)


@pytest.mark.slow
def test_long_text_is_truncated(embedder):
    [v] = embedder.embed(["palavra " * 20000])
    assert len(v) == 1024


@pytest.mark.slow
def test_cross_language_similarity(embedder):
    a, b, c = embedder.embed(
        [
            "Engenheiro de software backend com Python",
            "Backend software engineer using Python",
            "Chef de cozinha em restaurante italiano",
        ]
    )
    assert _cos(a, b) > _cos(a, c)
