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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    registry = JobRegistry()
    registry.register("heartbeat", heartbeat, 10)
    logger.info("worker started")
    while True:
        await registry.run_due(time.monotonic())
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
