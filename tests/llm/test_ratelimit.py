import pytest

from rumo_core.llm.ratelimit import RateLimiter


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make(n):
    t = FakeTime()
    return t, RateLimiter(n, clock=t.clock, sleep=t.sleep)


async def test_allows_burst_up_to_limit():
    t, rl = make(3)
    for _ in range(3):
        await rl.acquire()
    assert t.sleeps == []


async def test_blocks_when_exceeded():
    t, rl = make(3)
    for _ in range(3):
        await rl.acquire()
    t.now = 10.0
    await rl.acquire()
    assert t.sleeps == [50.0]
    assert t.now == 60.0


async def test_window_slides():
    t, rl = make(3)
    for _ in range(3):
        await rl.acquire()
    t.now = 61.0
    for _ in range(3):
        await rl.acquire()
    assert t.sleeps == []


@pytest.mark.parametrize("n", [0, -1])
def test_non_positive_max_raises(n):
    with pytest.raises(ValueError):
        RateLimiter(n)
