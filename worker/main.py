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


def build_llm(settings):
    """Real Groq provider, or None when no API key is configured."""
    if not settings.groq_api_key:
        return None
    import httpx

    from rumo_core.llm.groq import GroqProvider
    from rumo_core.llm.ratelimit import RateLimiter

    return GroqProvider(
        settings, httpx.AsyncClient(timeout=60), RateLimiter(settings.groq_max_per_minute)
    )


def build_collect_job(settings, session_factory):
    """collect_companies bound to the settings; at most
    collect_max_companies_per_run companies per tick so the sequential job loop
    stays responsive."""
    from functools import partial

    from worker.collect_jobs import collect_companies

    return partial(
        collect_companies,
        session_factory,
        interval_hours=settings.collect_interval_hours,
        delay_seconds=settings.collect_delay_seconds,
        max_companies=settings.collect_max_companies_per_run,
    )


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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from functools import partial

    from rumo_core.config import get_settings
    from rumo_core.db import SessionLocal
    from rumo_core.embeddings.bge_m3 import BgeM3Embedder
    from rumo_core.sources import aclose_sources
    from worker.interview_jobs import process_interview_turns, reset_working_sessions
    from worker.jobs import embed_pending_chunks, process_pending_imports, reset_stuck_imports

    settings = get_settings()
    reset = await retry_startup(lambda: reset_stuck_imports(SessionLocal))
    if reset:
        logger.info("reset %d stuck imports to pending", reset)
    reset_iv = await retry_startup(lambda: reset_working_sessions(SessionLocal))
    if reset_iv:
        logger.info("reset %d working interview sessions to generating", reset_iv)
    llm = build_llm(settings)
    embedder = BgeM3Embedder()  # model loads lazily, only when chunks are pending
    registry = JobRegistry()
    registry.register("heartbeat", heartbeat, 10)
    registry.register(
        "extract_imports",
        partial(process_pending_imports, SessionLocal, llm, settings.extract_max_chars),
        3,
    )
    registry.register(
        "interview_turns", partial(process_interview_turns, SessionLocal, llm), 3
    )
    registry.register(
        "embed_chunks",
        partial(embed_pending_chunks, SessionLocal, embedder, settings.embed_batch_size),
        5,
    )
    # every 60 s; per-company next_run_at governs the real collection frequency
    registry.register("collect_companies", build_collect_job(settings, SessionLocal), 60)
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
