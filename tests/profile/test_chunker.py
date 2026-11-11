import hashlib

import pytest

from rumo_core.profile.chunker import build_chunks
from rumo_core.profile.schema import CanonicalProfile, assign_ids


def _profile(**kw):
    return assign_ids(CanonicalProfile.model_validate(kw))


def test_one_chunk_per_experience():
    p = _profile(experiences=[
        {"company": "Acme", "title": "Dev", "start": "2020", "end": "2022",
         "description": "API", "achievements": [{"text": "Cortou custo"}]},
        {"company": "Beta", "title": "Lead"},
    ])
    chunks = [c for c in build_chunks(p, None) if c.kind == "experience"]
    assert len(chunks) == 2
    assert {c.source_ref for c in chunks} == {e.id for e in p.experiences}
    assert "Acme" in chunks[0].text and "Cortou custo" in chunks[0].text
    assert "2020" in chunks[0].text


def test_empty_sections_omitted():
    assert build_chunks(CanonicalProfile(), None) == []
    p = _profile(skills=[{"name": "Python"}])
    chunks = build_chunks(p, "  ")
    assert [c.source_ref for c in chunks] == ["skills"]


def test_summary_and_other_kinds():
    p = _profile(
        summary="Resumo", education=[{"institution": "USP", "degree": "BSc"}],
        languages=[{"name": "Ingles", "level": "C1"}],
        projects=[{"name": "X"}], certifications=[{"name": "AWS"}],
    )
    chunks = build_chunks(p, "Narrativa")
    kinds = [c.kind for c in chunks]
    assert kinds == ["summary", "education", "skills", "project", "certification"]
    assert "Narrativa" in chunks[0].text
    assert "Ingles" in chunks[2].text


def test_chunks_deterministic():
    p = _profile(
        summary="s", experiences=[{"company": "A", "title": "B"}],
        skills=[{"name": "Go"}],
    )
    assert build_chunks(p, "n") == build_chunks(p, "n")
    c = build_chunks(p, "n")[0]
    assert c.content_hash == hashlib.sha256(c.text.encode()).hexdigest()


def test_missing_id_raises():
    p = CanonicalProfile.model_validate({"experiences": [{"company": "A"}]})
    with pytest.raises(ValueError):
        build_chunks(p, None)
