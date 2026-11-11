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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from functools import partial

    from rumo_core.config import get_settings
    from rumo_core.db import SessionLocal
    from rumo_core.embeddings.bge_m3 import BgeM3Embedder
    from worker.jobs import embed_pending_chunks, process_pending_imports, reset_stuck_imports

    settings = get_settings()
    reset = reset_stuck_imports(SessionLocal)
    if reset:
        logger.info("reset %d stuck imports to pending", reset)
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
        "embed_chunks",
        partial(embed_pending_chunks, SessionLocal, embedder, settings.embed_batch_size),
        5,
    )
    logger.info("worker started")
    while True:
        await registry.run_due(time.monotonic())
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
