"""Profile confirmation: idempotent, versioned, with embedding reuse."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rumo_core.models import Profile, ProfileChunk
from rumo_core.profile.chunker import build_chunks
from rumo_core.profile.schema import CanonicalProfile


def _fill_empty_ids(profile: CanonicalProfile) -> CanonicalProfile:
    """Deterministically fill EMPTY ids (existing ids are kept), so confirming
    the same content twice yields the same ids and hash. Id = sha1 of the
    section and item content, plus an occurrence counter for identical items."""
    out = profile.model_copy(deep=True)
    seen: dict[str, int] = {}

    def make(section: str, item) -> str:
        content = json.dumps(
            item.model_dump(exclude={"id"}), sort_keys=True, ensure_ascii=False
        )
        key = f"{section}:{content}"
        n = seen.get(key, 0)
        seen[key] = n + 1
        return hashlib.sha1(f"{key}:{n}".encode("utf-8")).hexdigest()[:8]

    for name in CanonicalProfile.model_fields:
        value = getattr(out, name)
        if not isinstance(value, list):
            continue
        for item in value:
            for ach in getattr(item, "achievements", []):
                if not ach.id:
                    ach.id = make(f"{name}.achievement", ach)
            if not item.id:
                item.id = make(name, item)
    return out


def _profile_hash(profile: CanonicalProfile, narrative: str | None) -> str:
    payload = {"profile": profile.model_dump(), "narrative": narrative or ""}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_current_profile(session: Session, user_id: int) -> Profile | None:
    return session.scalars(
        select(Profile)
        .where(Profile.user_id == user_id)
        .order_by(Profile.version.desc())
        .limit(1)
    ).first()


def _create_version(
    session: Session, user_id: int, profile: CanonicalProfile,
    narrative: str | None, content_hash: str, latest: Profile | None,
) -> Profile:
    version = (latest.version if latest else 0) + 1
    previous: dict[str, ProfileChunk] = {}
    if latest:
        previous = {
            c.source_ref: c
            for c in session.scalars(
                select(ProfileChunk).where(
                    ProfileChunk.user_id == user_id,
                    ProfileChunk.profile_version == latest.version,
                )
            )
        }
    row = Profile(
        user_id=user_id, version=version, data=profile.model_dump(),
        narrative=narrative, content_hash=content_hash,
    )
    session.add(row)
    for ch in build_chunks(profile, narrative):
        old = previous.get(ch.source_ref)
        reuse = old is not None and old.content_hash == ch.content_hash
        session.add(ProfileChunk(
            user_id=user_id, profile_version=version,
            source_ref=ch.source_ref, kind=ch.kind, text=ch.text,
            content_hash=ch.content_hash,
            embedding=old.embedding if reuse else None,
            model_version=old.model_version if reuse else None,
        ))
    session.flush()
    return row


def confirm_profile(
    session: Session, user_id: int, profile: CanonicalProfile,
    narrative: str | None,
) -> Profile:
    """Confirm a profile. Idempotent against the latest version only: same
    hash as latest returns it; otherwise creates version max+1 (even if an
    older version had the same hash)."""
    profile = _fill_empty_ids(profile)
    content_hash = _profile_hash(profile, narrative)
    for attempt in range(2):
        latest = get_current_profile(session, user_id)
        if latest is not None and latest.content_hash == content_hash:
            return latest
        try:
            row = _create_version(
                session, user_id, profile, narrative, content_hash, latest
            )
            session.commit()
            return row
        except IntegrityError:
            # Lost a race on (user_id, version); re-read and retry once.
            session.rollback()
            if attempt == 1:
                raise
        except Exception:
            session.rollback()  # e.g. ValueError from build_chunks
            raise
    raise RuntimeError("unreachable")  # pragma: no cover
