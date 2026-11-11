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
