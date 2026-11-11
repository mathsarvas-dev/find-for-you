import asyncio
import time
from collections import deque

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding-window limiter: at most max_per_minute acquisitions per 60 s."""

    def __init__(self, max_per_minute: int, clock=time.monotonic, sleep=asyncio.sleep):
        self.max = max_per_minute
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= WINDOW_SECONDS:
                    self._stamps.popleft()
                if len(self._stamps) < self.max:
                    self._stamps.append(now)
                    return
                await self._sleep(self._stamps[0] + WINDOW_SECONDS - now)
