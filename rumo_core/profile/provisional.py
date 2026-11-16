"""Provisional profile: raw resume text split into chunks, no LLM.

The chunks are embedded by the worker like any other profile chunk, so
collection, filters and similarity ranking work. The profile data itself is
empty (flag `provisional`), so applications must not use it.
"""

import hashlib
import re

from sqlalchemy.orm import Session

from rumo_core.models import Profile, ProfileChunk
from rumo_core.profile.locks import lock_user_profile
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile

MAX_CHUNK_CHARS = 1200
MAX_CHUNKS = 40


class ProfileAlreadyStructured(Exception):
    pass


def split_raw_text(text: str) -> list[str]:
    """Paragraph-based chunks (blank-line separated); small paragraphs are
    merged up to MAX_CHUNK_CHARS and oversized ones are cut on whitespace."""
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    pieces: list[str] = []
    for p in filter(None, paras):
        while len(p) > MAX_CHUNK_CHARS:
            cut = p.rfind(" ", 0, MAX_CHUNK_CHARS)
            cut = cut if cut > 0 else MAX_CHUNK_CHARS
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            pieces.append(p)
    chunks: list[str] = []
    for p in pieces:
        if chunks and len(chunks[-1]) + len(p) + 2 <= MAX_CHUNK_CHARS:
            chunks[-1] += "\n\n" + p
        else:
            chunks.append(p)
    return chunks[:MAX_CHUNKS]


def create_provisional_profile(
    session: Session, user_id: int, raw_text: str
) -> Profile:
    chunks = split_raw_text(raw_text)
    if not chunks:
        raise ValueError("empty text")
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    lock_user_profile(session, user_id)
    latest = get_current_profile(session, user_id)
    if latest is not None and not latest.provisional:
        raise ProfileAlreadyStructured()
    if latest is not None and latest.content_hash == content_hash:
        return latest
    version = (latest.version if latest else 0) + 1
    row = Profile(
        user_id=user_id, version=version, provisional=True,
        data=CanonicalProfile().model_dump(), narrative=None,
        content_hash=content_hash,
    )
    session.add(row)
    for i, text in enumerate(chunks):
        session.add(ProfileChunk(
            user_id=user_id, profile_version=version, source_ref=f"raw-{i}",
            kind="raw", text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ))
    session.commit()
    return row
