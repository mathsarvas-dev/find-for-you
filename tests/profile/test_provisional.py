from rumo_core.profile.provisional import MAX_CHUNK_CHARS, split_raw_text


def test_merges_small_paragraphs_and_splits_large():
    chunks = split_raw_text("Nome\n\nResumo curto\n\n" + "palavra " * 400)
    assert chunks[0].startswith("Nome\n\nResumo curto")
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
    assert len(chunks) >= 3


def test_empty_text_has_no_chunks():
    assert split_raw_text("  \n\n ") == []
