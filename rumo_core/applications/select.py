"""Evidence per requirement: the profile chunks nearest to each requirement vector.

The only DB-touching module of the applications package. It reads the chunks
of one profile version, with the same model version and non-zero vectors that
the ranking uses (`_chunk_conds`).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from rumo_core.matching.ranking import _chunk_conds
from rumo_core.models import ProfileChunk


def select_evidence(
    session: Session,
    user_id: int,
    profile_version: int,
    model_version: str,
    req_vectors: dict[str, list[float]],
    k: int = 2,
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for req_id, vector in req_vectors.items():
        distance = ProfileChunk.embedding.cosine_distance(vector)
        refs = session.execute(
            select(ProfileChunk.source_ref)
            .where(*_chunk_conds(user_id, profile_version, model_version))
            .order_by(distance)
            .limit(k)
        ).scalars().all()
        out[req_id] = list(refs)
    return out
