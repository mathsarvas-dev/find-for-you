"""Worker job: vectorize the free-text query of search profiles.

The query text is private user input: it is never logged (only the exception
class and the batch size)."""

import asyncio
import logging
import math
import time

from sqlalchemy import or_, select, update

from rumo_core.embeddings.base import Embedder
from rumo_core.models import SearchProfile
from worker.jobs import EMBED_BACKOFF_BASE, EMBED_BACKOFF_CAP, EMBED_LOCK

logger = logging.getLogger("worker.embed_queries")

# In-process backoff state (single worker process), separate from the other
# embedding jobs.
_next_try = 0.0
_failures = 0


def _reset_state() -> None:
    """Reset the backoff state (used by tests)."""
    global _next_try, _failures
    _next_try = 0.0
    _failures = 0


async def embed_pending_queries(
    session_factory,
    embedder: Embedder,
    *,
    batch: int = 8,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Embed up to `batch` pending query texts. Returns how many were written.

    Pending: query_text set and (no embedding, or model_version differs).
    The read transaction is closed before the embed call (no DB lock is held
    while the model runs). Each vector is written only if query_text is still
    exactly the text it was computed from; otherwise it is skipped and the
    profile is picked up again on a later tick.

    On embedder failure only the exception class and batch size are logged and
    the job backs off exponentially (5s doubling, cap 300s, reset on success).
    """
    global _next_try, _failures
    if clock() < _next_try:
        return 0
    with session_factory() as s:
        stmt = select(SearchProfile.id, SearchProfile.query_text).where(
            SearchProfile.query_text.is_not(None),
            or_(
                SearchProfile.query_embedding.is_(None),
                SearchProfile.query_model_version.is_distinct_from(
                    embedder.model_version
                ),
            ),
        )
        if user_id is not None:
            stmt = stmt.where(SearchProfile.user_id == user_id)
        rows = [
            (r.id, r.query_text)
            for r in s.execute(stmt.order_by(SearchProfile.is_default.desc(), SearchProfile.query_embedding.is_(None).desc(), SearchProfile.id).limit(batch)).all()
        ]
        s.commit()
    if not rows:
        return 0
    def fail(reason: str, clock=clock) -> int:
        global _next_try, _failures
        delay = min(EMBED_BACKOFF_BASE * 2**_failures, EMBED_BACKOFF_CAP)
        _failures += 1
        _next_try = clock() + delay
        # never the exception message/traceback: it can carry the query text
        logger.error(
            "query embedding failed (%s, batch of %d); backing off %ds",
            reason, len(rows), delay,
        )
        return 0

    try:
        async with EMBED_LOCK:
            vectors = await asyncio.to_thread(embedder.embed, [t for _, t in rows])
    except Exception as exc:
        return fail(type(exc).__name__)
    dims = getattr(embedder, "dimensions", 1024)
    try:
        valid = len(vectors) == len(rows) and all(
            len(v) == dims
            and math.isfinite(n := math.sqrt(sum(float(x) ** 2 for x in v)))
            and n > 0
            for v in vectors
        )
    except Exception as exc:
        return fail(type(exc).__name__)
    if not valid:
        return fail("InvalidEmbedderOutput")
    try:
        written = 0
        with session_factory() as s:
            for (pid, text), vec in zip(rows, vectors, strict=True):
                res = s.execute(
                    update(SearchProfile)
                    .where(SearchProfile.id == pid, SearchProfile.query_text == text)
                    .values(
                        query_embedding=list(vec),
                        query_model_version=embedder.model_version,
                    )
                )
                written += res.rowcount
            s.commit()
    except Exception as exc:
        return fail(type(exc).__name__)
    _failures = 0
    return written
