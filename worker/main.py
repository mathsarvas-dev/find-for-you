import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger("worker")


class JobRegistry:
    def __init__(self) -> None:
        # name -> (fn, every_seconds, next_run)
        self._jobs: dict[str, list] = {}

    def register(
        self, name: str, fn: Callable[[], Awaitable[None]], every_seconds: int
    ) -> None:
        self._jobs[name] = [fn, every_seconds, None]

    async def run_due(self, now: float) -> list[str]:
        ran: list[str] = []
        for name, job in self._jobs.items():
            fn, every, next_run = job
            if next_run is not None and now < next_run:
                continue
            job[2] = now + every
            ran.append(name)
            try:
                await fn()
            except Exception:
                logger.exception("job %s failed", name)
        return ran


async def heartbeat() -> None:
    logger.info("heartbeat")


def build_llm(settings, *, model: str | None = None):
    """Real Groq provider, or None when no API key is configured. `model`
    overrides settings.groq_model (a separate model has its own token quota)."""
    if not settings.groq_api_key:
        return None
    import httpx

    from rumo_core.llm.groq import GroqProvider
    from rumo_core.llm.ratelimit import RateLimiter

    if model:
        settings = settings.model_copy(update={"groq_model": model})
    return GroqProvider(
        settings, httpx.AsyncClient(timeout=60), RateLimiter(settings.groq_max_per_minute)
    )


def build_collect_job(settings, session_factory):
    """collect_companies bound to the settings; at most
    collect_max_companies_per_run companies and collect_max_seconds per tick
    so the sequential job loop stays responsive."""
    from functools import partial

    from worker.collect_jobs import collect_companies

    return partial(
        collect_companies,
        session_factory,
        interval_hours=settings.collect_interval_hours,
        delay_seconds=settings.collect_delay_seconds,
        max_companies=settings.collect_max_companies_per_run,
        max_seconds=settings.collect_max_seconds,
    )


def build_collect_feeds_job(settings, session_factory):
    """collect_feeds bound to the settings (behind the compliance gate, see worker.collect_feeds)."""
    from functools import partial

    from worker.collect_feeds import collect_feeds

    return partial(
        collect_feeds,
        session_factory,
        interval_hours=settings.collect_interval_hours,
        delay_seconds=settings.collect_delay_seconds,
        ttl_days=settings.feed_job_ttl_days,
        max_feeds=settings.collect_feeds_max_per_run,
        max_seconds=settings.collect_max_seconds,
        settings=settings,
    )


def build_discover_job(settings, session_factory):
    """discover_companies bound to the discovery_* settings. The pause between
    board fetches reuses collect_delay_seconds (same Greenhouse host)."""
    from functools import partial

    from worker.discover_companies import discover_companies

    return partial(
        discover_companies,
        session_factory,
        batch=settings.discovery_batch,
        max_seconds=settings.discovery_max_seconds,
        max_auto=settings.discovery_max_auto_companies,
        delay=settings.collect_delay_seconds,
        max_attempts=settings.discovery_max_attempts,
    )


def build_embed_jobs_job(settings, session_factory, embedder):
    """embed_pending_jobs limited to ONE batch of job_embed_batch_size per
    tick: a backlog (~0.4 texts/s on CPU) never holds the sequential loop
    (and so the interview turns) for more than a few seconds."""
    from functools import partial

    from worker.collect_jobs import embed_pending_jobs

    return partial(
        embed_pending_jobs, session_factory, embedder, settings.job_embed_batch_size,
        max_batches=1,
    )


def build_score_matches_job(settings, session_factory, llm):
    """score_pending_matches bound to the match_* settings (batch, attempts,
    rolling-24h cap, per-call timeout)."""
    from functools import partial

    from worker.match_jobs import score_pending_matches

    return partial(
        score_pending_matches, session_factory, llm,
        batch=settings.match_batch, max_attempts=settings.match_max_attempts,
        daily_limit=settings.match_daily_limit,
        call_timeout=settings.match_call_timeout,
    )


def build_prepare_applications_job(settings, session_factory, llm, embedder):
    """prepare_applications bound to the application_* settings (one application per tick)."""
    from functools import partial

    from worker.prepare_applications import prepare_applications

    return partial(
        prepare_applications, session_factory, llm, embedder,
        max_attempts=settings.application_max_attempts,
        daily_limit=settings.application_daily_limit,
        call_timeout=settings.application_call_timeout,
    )


def build_registry(settings, session_factory, llm, embedder, fill_llm=None) -> JobRegistry:
    """All worker jobs, in priority order (run_due runs them in this order)."""
    from functools import partial

    from worker.collect_jobs import fill_missing_job_fields
    from worker.embed_queries import embed_pending_queries
    from worker.interview_jobs import process_interview_turns
    from worker.jobs import embed_pending_chunks, process_pending_imports

    registry = JobRegistry()
    registry.register("heartbeat", heartbeat, 10)
    registry.register(
        "extract_imports",
        partial(process_pending_imports, session_factory, llm, settings.extract_max_chars),
        3,
    )
    registry.register(
        "interview_turns", partial(process_interview_turns, session_factory, llm), 3
    )
    # right after the interview turns (the interview keeps priority on the LLM);
    # one application per tick, progress is polled by the page
    registry.register(
        "prepare_applications",
        build_prepare_applications_job(settings, session_factory, llm, embedder), 5,
    )
    # every 60 s with a small batch: the Groq rate limiter is shared with the
    # interview, whose turns keep priority (registered after interview_turns)
    registry.register(
        "fill_missing_job_fields",
        partial(
            fill_missing_job_fields, session_factory,
            fill_llm if fill_llm is not None else llm, batch=settings.llm_fill_batch,
        ),
        60,
    )
    # same limiter, after the field fill (filters use the filled fields)
    registry.register(
        "score_matches", build_score_matches_job(settings, session_factory, llm), 60
    )
    registry.register(
        "embed_chunks",
        partial(embed_pending_chunks, session_factory, embedder, settings.embed_batch_size),
        5,
    )
    # search-phrase vectors: small batches, the user is waiting on the result
    registry.register(
        "embed_queries",
        partial(
            embed_pending_queries, session_factory, embedder, batch=settings.embed_batch_size
        ),
        15,
    )
    # after the embed_chunks/embed_queries jobs (profile first); one small batch per tick so a long
    # backlog (~0.4 texts/s on CPU) never starves the other jobs
    registry.register(
        "embed_jobs", build_embed_jobs_job(settings, session_factory, embedder), 5
    )
    # every 60 s; per-company next_run_at governs the real collection frequency
    registry.register("collect_companies", build_collect_job(settings, session_factory), 60)
    # same queue and fairness as the companies: feeds are gated and rate limited per host
    registry.register("collect_feeds", build_collect_feeds_job(settings, session_factory), 60)
    # right after the collection: new auto companies get next_run_at=now and
    # are collected on the next tick
    registry.register("discover_companies", build_discover_job(settings, session_factory), 60)
    return registry


async def retry_startup(
    fn: Callable[[], object],
    attempts: int = 10,
    delay: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
):
    """Call fn, retrying on any exception (e.g. DB up but migrations not yet
    applied). Re-raises the last error once attempts are exhausted."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception:
            logger.exception("startup failed (attempt %d/%d)", attempt, attempts)
            if attempt == attempts:
                raise
            await sleep(delay)


def configure_logging() -> None:
    """Root INFO logging, but never httpx/httpcore at INFO: they log every
    request URL, which carries board tokens derived from profile company names."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def main() -> None:
    configure_logging()
    from rumo_core.config import get_settings
    from rumo_core.db import SessionLocal
    from rumo_core.embeddings.bge_m3 import BgeM3Embedder
    from rumo_core.sources import aclose_sources
    from worker.interview_jobs import reset_working_sessions
    from worker.jobs import reset_stuck_imports

    settings = get_settings()
    reset = await retry_startup(lambda: reset_stuck_imports(SessionLocal))
    if reset:
        logger.info("reset %d stuck imports to pending", reset)
    reset_iv = await retry_startup(lambda: reset_working_sessions(SessionLocal))
    if reset_iv:
        logger.info("reset %d working interview sessions to generating", reset_iv)
    llm = build_llm(settings)
    embedder = BgeM3Embedder()  # model loads lazily, only when chunks are pending
    fill_llm = build_llm(settings, model=settings.groq_fill_model or None)
    registry = build_registry(settings, SessionLocal, llm, embedder, fill_llm)
    logger.info("worker started")
    try:
        while True:
            await registry.run_due(time.monotonic())
            await asyncio.sleep(1)
    finally:
        # also runs on KeyboardInterrupt (asyncio.run cancels this task)
        await aclose_sources()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
