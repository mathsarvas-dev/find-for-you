"""Worker jobs: resume extraction and chunk embedding."""

import asyncio
import logging
import time

from sqlalchemy import func, or_, select, update

from rumo_core.embeddings.base import Embedder
from rumo_core.llm.base import LLMError, LLMInvalidResponse, LLMProvider, LLMRateLimited
from rumo_core.models import ProfileChunk, ProfileImport
from rumo_core.profile.extraction import extract_profile

logger = logging.getLogger("worker.jobs")

_embed_lock = asyncio.Lock()
# public alias: the same lock serialises every CPU-bound embedding call
EMBED_LOCK = _embed_lock

MSG_INVALID = "Não foi possível interpretar o currículo. Tente outro arquivo."
MSG_LLM_FAIL = "Falha ao processar o currículo. Tente novamente mais tarde."
MSG_UNEXPECTED = "Falha inesperada ao ler o currículo."

RATE_LIMIT_PAUSE_SECONDS = 60.0
EMBED_BACKOFF_BASE = 5.0
EMBED_BACKOFF_CAP = 300.0

# In-process state (single worker process).
_warned_no_llm = False
_extract_pause_until = 0.0
_embed_next_try = 0.0
_embed_failures = 0


def _reset_state() -> None:
    """Reset in-process cool-down/backoff state (used by tests)."""
    global _warned_no_llm, _extract_pause_until, _embed_next_try, _embed_failures
    _warned_no_llm = False
    _extract_pause_until = 0.0
    _embed_next_try = 0.0
    _embed_failures = 0


def reset_stuck_imports(session_factory, *, user_id: int | None = None) -> int:
    """On startup: imports left 'extracting' by a dead worker go back to 'pending'.

    Assumes a single worker process: with several workers this would also reset
    imports another live worker is processing.
    """
    with session_factory() as s:
        stmt = update(ProfileImport).where(ProfileImport.status == "extracting")
        if user_id is not None:
            stmt = stmt.where(ProfileImport.user_id == user_id)
        res = s.execute(stmt.values(status="pending"))
        s.commit()
        return res.rowcount


def _claim_import(
    session_factory, user_id: int | None = None
) -> tuple[int, str, str] | None:
    with session_factory() as s:
        stmt = select(ProfileImport).where(ProfileImport.status == "pending")
        if user_id is not None:
            stmt = stmt.where(ProfileImport.user_id == user_id)
        row = s.execute(
            stmt.order_by(ProfileImport.id).limit(1).with_for_update(skip_locked=True)
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
    session_factory,
    llm: LLMProvider | None,
    max_chars: int,
    *,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Process at most one pending import per call. Returns how many were processed.

    With no LLM configured (no API key) imports stay 'pending'; warns once.
    After a rate limit the job pauses itself for RATE_LIMIT_PAUSE_SECONDS.
    Any unexpected (non-LLM) error marks that import 'error' so it cannot
    block the ones behind it.
    """
    global _warned_no_llm, _extract_pause_until
    if llm is None:
        if not _warned_no_llm:
            logger.warning("GROQ_API_KEY not set: resume imports stay pending")
            _warned_no_llm = True
        return 0
    if clock() < _extract_pause_until:
        return 0
    claimed = _claim_import(session_factory, user_id)
    if claimed is None:
        return 0
    import_id, text, _filename = claimed
    try:
        profile, truncated = await extract_profile(llm, text, max_chars)
    except LLMRateLimited:
        _extract_pause_until = clock() + RATE_LIMIT_PAUSE_SECONDS
        logger.warning(
            "LLM rate limited; pausing extraction for %ds", RATE_LIMIT_PAUSE_SECONDS
        )
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
    except Exception:
        logger.exception("import %s unexpected failure", import_id)
        _finish_import(
            session_factory, import_id, status="error", error=MSG_UNEXPECTED, extracted=None
        )
        return 1
    except BaseException:
        # cancellation: do not leave it stuck in 'extracting'
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
    session_factory,
    embedder: Embedder,
    batch_size: int,
    *,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Embed chunks needing work, in batches (one commit per batch).

    Needing work: embedding IS NULL or model_version differs from the embedder's.
    Order: chunks of each user's current (max) profile_version first, then
    older versions (low priority), then by id. Restart resumes naturally.

    On embedder failure the traceback is logged once and the job backs off
    exponentially (5s doubling, cap 300s, reset on success) instead of raising.
    """
    global _embed_next_try, _embed_failures
    if clock() < _embed_next_try:
        return 0
    total = 0
    while True:
        with session_factory() as s:
            cur = (
                select(
                    ProfileChunk.user_id,
                    func.max(ProfileChunk.profile_version).label("v"),
                )
                .group_by(ProfileChunk.user_id)
                .subquery()
            )
            stmt = (
                select(ProfileChunk)
                .join(cur, cur.c.user_id == ProfileChunk.user_id)
                .where(
                    or_(
                        ProfileChunk.embedding.is_(None),
                        ProfileChunk.model_version.is_distinct_from(
                            embedder.model_version
                        ),
                    )
                )
            )
            if user_id is not None:
                stmt = stmt.where(ProfileChunk.user_id == user_id)
            rows = s.execute(
                stmt.order_by(
                    (ProfileChunk.profile_version == cur.c.v).desc(),
                    ProfileChunk.id,
                )
                .limit(batch_size)
                .with_for_update(of=ProfileChunk, skip_locked=True)
            ).scalars().all()
            if not rows:
                return total
            try:
                async with _embed_lock:
                    vectors = await asyncio.to_thread(
                        embedder.embed, [r.text for r in rows]
                    )
            except Exception:
                s.rollback()
                delay = min(EMBED_BACKOFF_BASE * 2**_embed_failures, EMBED_BACKOFF_CAP)
                _embed_failures += 1
                _embed_next_try = clock() + delay
                logger.exception("embedding failed; backing off %ds", delay)
                return total
            for r, v in zip(rows, vectors, strict=True):
                r.embedding = v
                r.model_version = embedder.model_version
            s.commit()
            _embed_failures = 0
            total += len(rows)
