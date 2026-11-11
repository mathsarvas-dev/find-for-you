"""Worker jobs: resume extraction and chunk embedding."""

import asyncio
import logging

from sqlalchemy import func, select, update

from rumo_core.embeddings.base import Embedder
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMProvider, LLMRateLimited
from rumo_core.models import ProfileChunk, ProfileImport
from rumo_core.profile.extraction import extract_profile

logger = logging.getLogger("worker.jobs")

_embed_lock = asyncio.Lock()
_warned_no_llm = False

MSG_INVALID = "Não foi possível interpretar o currículo. Tente outro arquivo."
MSG_LLM_FAIL = "Falha ao processar o currículo. Tente novamente mais tarde."


def reset_stuck_imports(session_factory) -> int:
    """On startup: imports left 'extracting' by a dead worker go back to 'pending'."""
    with session_factory() as s:
        res = s.execute(
            update(ProfileImport)
            .where(ProfileImport.status == "extracting")
            .values(status="pending")
        )
        s.commit()
        return res.rowcount


def _claim_import(session_factory) -> tuple[int, str, str] | None:
    with session_factory() as s:
        row = s.execute(
            select(ProfileImport)
            .where(ProfileImport.status == "pending")
            .order_by(ProfileImport.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "extracting"
        s.commit()
        return row.id, row.raw_text or "", row.filename


def _finish_import(session_factory, import_id: int, **values) -> None:
    with session_factory() as s:
        s.execute(
            update(ProfileImport).where(ProfileImport.id == import_id).values(**values)
        )
        s.commit()


async def process_pending_imports(
    session_factory, llm: LLMProvider | None, max_chars: int
) -> int:
    """Process at most one pending import per call. Returns how many were processed.

    With no LLM configured (no API key) imports stay 'pending'; warns once.
    """
    global _warned_no_llm
    if llm is None:
        if not _warned_no_llm:
            logger.warning("GROQ_API_KEY not set: resume imports stay pending")
            _warned_no_llm = True
        return 0
    claimed = _claim_import(session_factory)
    if claimed is None:
        return 0
    import_id, text, _filename = claimed
    try:
        profile, truncated = await extract_profile(llm, text, max_chars)
    except LLMRateLimited:
        logger.warning("import %s rate limited; will retry", import_id)
        _finish_import(session_factory, import_id, status="pending")
        return 0
    except LLMInvalidResponse:
        _finish_import(
            session_factory, import_id, status="error", error=MSG_INVALID, extracted=None
        )
        return 1
    except LLMError:
        logger.exception("import %s LLM failure", import_id)
        _finish_import(
            session_factory, import_id, status="error", error=MSG_LLM_FAIL, extracted=None
        )
        return 1
    except BaseException:
        # cancellation or unexpected failure: do not leave it stuck in 'extracting'
        _finish_import(session_factory, import_id, status="pending")
        raise
    _finish_import(
        session_factory,
        import_id,
        status="ready",
        extracted=profile.model_dump(mode="json"),
        truncated=truncated,
        error=None,
    )
    return 1


async def embed_pending_chunks(
    session_factory, embedder: Embedder, batch_size: int
) -> int:
    """Embed chunks with embedding IS NULL, in batches (one commit per batch).

    Order: chunks of each user's current (max) profile_version first, then
    older versions (low priority), then by id. Restart resumes naturally.
    """
    total = 0
    while True:
        with session_factory() as s:
            cur = (
                select(ProfileChunk.user_id, func.max(ProfileChunk.profile_version).label("v"))
                .group_by(ProfileChunk.user_id)
                .subquery()
            )
            rows = s.execute(
                select(ProfileChunk)
                .join(cur, cur.c.user_id == ProfileChunk.user_id)
                .where(ProfileChunk.embedding.is_(None))
                .order_by(
                    (ProfileChunk.profile_version == cur.c.v).desc(),
                    ProfileChunk.id,
                )
                .limit(batch_size)
                .with_for_update(of=ProfileChunk, skip_locked=True)
            ).scalars().all()
            if not rows:
                return total
            async with _embed_lock:
                vectors = await asyncio.to_thread(embedder.embed, [r.text for r in rows])
            for r, v in zip(rows, vectors, strict=True):
                r.embedding = v
                r.model_version = embedder.model_version
            s.commit()
            total += len(rows)
