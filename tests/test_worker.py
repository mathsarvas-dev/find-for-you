from worker.main import JobRegistry


async def test_runs_job_when_due():
    reg = JobRegistry()
    calls = []

    async def job():
        calls.append(1)

    reg.register("j", job, 60)
    assert await reg.run_due(0) == ["j"]
    assert await reg.run_due(30) == []
    assert await reg.run_due(60) == ["j"]
    assert len(calls) == 2


async def test_failing_job_does_not_stop_others():
    reg = JobRegistry()
    ran = []

    async def a():
        raise RuntimeError("boom")

    async def b():
        ran.append("b")

    reg.register("a", a, 10)
    reg.register("b", b, 10)
    done = await reg.run_due(0)
    assert "b" in done
    assert ran == ["b"]


async def test_failing_job_is_retried_next_interval():
    reg = JobRegistry()
    attempts = []

    async def a():
        attempts.append(1)
        raise RuntimeError("boom")

    reg.register("a", a, 10)
    await reg.run_due(0)
    assert await reg.run_due(5) == []
    await reg.run_due(10)
    assert len(attempts) == 2


async def test_retry_startup_retries_then_succeeds():
    from worker.main import retry_startup

    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("no table yet")
        return 7

    async def fake_sleep(d):
        sleeps.append(d)

    assert await retry_startup(flaky, attempts=5, delay=2.0, sleep=fake_sleep) == 7
    assert len(calls) == 3 and sleeps == [2.0, 2.0]


async def test_retry_startup_raises_after_exhausting_attempts():
    import pytest

    from worker.main import retry_startup

    calls, sleeps = [], []

    def broken():
        calls.append(1)
        raise RuntimeError("down")

    async def fake_sleep(d):
        sleeps.append(d)

    with pytest.raises(RuntimeError):
        await retry_startup(broken, attempts=3, delay=1.0, sleep=fake_sleep)
    assert len(calls) == 3 and len(sleeps) == 2
