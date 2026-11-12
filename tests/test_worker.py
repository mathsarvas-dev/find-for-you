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


def test_collect_job_registration_uses_settings():
    from rumo_core.config import Settings
    from worker.collect_jobs import collect_companies
    from worker.main import build_collect_job

    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://u:p@localhost/x",
        collect_interval_hours=4, collect_delay_seconds=0.5,
        collect_max_companies_per_run=7, collect_max_seconds=45,
    )
    sf = object()
    job = build_collect_job(settings, sf)
    assert job.func is collect_companies and job.args == (sf,)
    assert job.keywords == {
        "interval_hours": 4, "delay_seconds": 0.5, "max_companies": 7, "max_seconds": 45,
    }


def test_collect_max_seconds_setting_default_and_positive():
    import pytest
    from pydantic import ValidationError

    from rumo_core.config import Settings

    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/x")
    assert s.collect_max_seconds == 60
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="x", collect_max_seconds=0)


def test_embed_jobs_registration_is_one_small_batch():
    """A job-embedding backlog (~0.4 texts/s on CPU) must not hold the
    sequential loop for more than a few seconds per tick."""
    from rumo_core.config import Settings
    from worker.collect_jobs import embed_pending_jobs
    from worker.main import build_embed_jobs_job

    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/x")
    assert s.job_embed_batch_size == 2 and s.embed_batch_size == 8
    sf, emb = object(), object()
    job = build_embed_jobs_job(s, sf, emb)
    assert job.func is embed_pending_jobs and job.args == (sf, emb, 2)
    assert job.keywords == {"max_batches": 1}
    s = Settings(_env_file=None, database_url="x", job_embed_batch_size=3)
    assert build_embed_jobs_job(s, sf, emb).args == (sf, emb, 3)
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="x", job_embed_batch_size=0)

