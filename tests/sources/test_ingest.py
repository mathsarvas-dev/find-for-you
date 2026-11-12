import dataclasses
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select

from rumo_core.db import SessionLocal, engine
from rumo_core.models import Company, Job, User
from rumo_core.sources.base import RawJob
from rumo_core.sources.ingest import IngestResult, SuspiciousEmptyResult, ingest_jobs

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)
T2 = T1 + timedelta(hours=6)


def raw(eid, title=None, loc="Remote, Brazil", desc="<p>Build backend services in Python.</p>",
        dept="Engineering"):
    return RawJob(
        external_id=str(eid), title=title or f"Engineer {eid}",
        url=f"https://example.test/jobs/{eid}", location_text=loc,
        description_html=desc, department=dept, posted_at=T0, raw={"id": eid},
    )


@pytest.fixture
def ctx():
    with SessionLocal() as s:
        u = User(email=f"ingest-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
        s.add(c)
        s.commit()
        uid, cid = u.id, c.id
    yield uid, cid
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == uid))
        s.execute(delete(Company).where(Company.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def run(ctx, raws, now):
    uid, cid = ctx
    with SessionLocal() as s:
        company = s.get(Company, cid)
        res = ingest_jobs(s, uid, company, raws, now=now)
        s.commit()
        return res


def jobs(ctx):
    uid, cid = ctx
    with SessionLocal() as s:
        return {j.external_id: j for j in s.scalars(
            select(Job).where(Job.user_id == uid, Job.company_id == cid)
        )}


def company(ctx):
    with SessionLocal() as s:
        return s.get(Company, ctx[1])


def test_new_jobs_created_open(ctx):
    res = run(ctx, [raw(1), raw(2)], T0)
    assert res == IngestResult(created=2, updated=0, unchanged=0, closed=0,
                               reopened=0, duplicates=0)
    js = jobs(ctx)
    assert set(js) == {"1", "2"}
    j = js["1"]
    assert j.status == "open" and j.first_seen_at == T0 and j.last_seen_at == T0
    assert j.description == "Build backend services in Python."
    assert j.raw == {"id": 1} and j.url == "https://example.test/jobs/1"
    assert j.country == "BR" and j.work_model == "remote"
    assert j.department == "Engineering" and j.posted_at == T0
    assert j.embedding is None and j.closed_at is None


def _embed(ctx, eid):
    uid, cid = ctx
    with SessionLocal() as s:
        j = s.scalars(select(Job).where(Job.user_id == uid, Job.company_id == cid,
                                        Job.external_id == eid)).one()
        j.embedding = [0.1] * 1024
        j.model_version = "m1"
        j.llm_filled_at = T0
        j.fill_attempts = 2
        s.commit()


def test_unchanged_job_only_touches_last_seen(ctx):
    run(ctx, [raw(1)], T0)
    _embed(ctx, "1")
    res = run(ctx, [raw(1)], T1)
    assert res.unchanged == 1 and res.updated == 0 and res.created == 0
    j = jobs(ctx)["1"]
    assert j.first_seen_at == T0 and j.last_seen_at == T1
    assert j.embedding is not None and j.model_version == "m1"
    assert j.llm_filled_at == T0 and j.fill_attempts == 2


def test_changed_description_resets_embedding_and_fill(ctx):
    run(ctx, [raw(1)], T0)
    _embed(ctx, "1")
    res = run(ctx, [raw(1, desc="<p>Now Go too. Remote.</p>")], T1)
    assert res.updated == 1 and res.unchanged == 0
    j = jobs(ctx)["1"]
    assert j.description == "Now Go too. Remote."
    assert j.embedding is None and j.model_version is None
    assert j.llm_filled_at is None and j.fill_attempts == 0
    assert j.first_seen_at == T0 and j.last_seen_at == T1 and j.status == "open"


def test_missing_jobs_closed(ctx):
    run(ctx, [raw(1), raw(2), raw(3)], T0)
    res = run(ctx, [raw(1)], T1)
    assert res.closed == 2
    js = jobs(ctx)
    assert js["1"].status == "open"
    for e in ("2", "3"):
        assert js[e].status == "closed" and js[e].closed_at == T1
        assert js[e].last_seen_at == T0


def test_closed_job_reappears_reopened(ctx):
    run(ctx, [raw(1), raw(2)], T0)
    run(ctx, [raw(1)], T1)
    res = run(ctx, [raw(1), raw(2)], T2)
    assert res.reopened == 1 and res.unchanged == 2
    j = jobs(ctx)["2"]
    assert j.status == "open" and j.closed_at is None and j.last_seen_at == T2


def test_closed_job_reappears_changed_is_reopened_and_updated(ctx):
    run(ctx, [raw(1), raw(2)], T0)
    run(ctx, [raw(1)], T1)
    res = run(ctx, [raw(1), raw(2, desc="<p>new text</p>")], T2)
    assert res.reopened == 1 and res.updated == 1
    j = jobs(ctx)["2"]
    assert j.status == "open" and j.closed_at is None and j.description == "new text"


def test_same_fingerprint_new_id_is_duplicate(ctx):
    run(ctx, [raw(1, title="Backend Engineer")], T0)
    res = run(ctx, [raw(1, title="Backend Engineer"),
                    raw(2, title="Backend  engineer!", desc="<p>other text</p>")], T1)
    assert res.created == 1 and res.duplicates == 1
    js = jobs(ctx)
    assert js["1"].status == "open" and js["2"].status == "duplicate"
    assert company(ctx).open_jobs == 1


def test_duplicate_promoted_when_original_closes(ctx):
    run(ctx, [raw(1, title="Backend Engineer")], T0)
    run(ctx, [raw(1, title="Backend Engineer"), raw(2, title="Backend Engineer")], T1)
    assert jobs(ctx)["2"].status == "duplicate"
    res = run(ctx, [raw(2, title="Backend Engineer")], T2)
    js = jobs(ctx)
    assert js["1"].status == "closed" and js["2"].status == "open"
    assert res.closed == 1 and res.duplicates == 0
    assert company(ctx).open_jobs == 1


def test_duplicate_promoted_when_original_changes_fingerprint(ctx):
    run(ctx, [raw(1, title="Backend Engineer")], T0)
    run(ctx, [raw(1, title="Backend Engineer"), raw(2, title="Backend Engineer")], T1)
    run(ctx, [raw(1, title="Staff Backend Engineer"), raw(2, title="Backend Engineer")], T2)
    js = jobs(ctx)
    assert js["1"].status == "open" and js["2"].status == "open"


def test_changed_job_colliding_with_open_incumbent_becomes_duplicate(ctx):
    run(ctx, [raw(1, title="Backend Engineer"), raw(2, title="Frontend Engineer")], T0)
    # job 1 (older or equal first_seen) changes its title onto job 2's fingerprint
    res = run(ctx, [raw(1, title="Frontend Engineer"), raw(2, title="Frontend Engineer")], T1)
    js = jobs(ctx)
    assert js["2"].status == "open" and js["1"].status == "duplicate"
    assert res.duplicates == 1 and res.updated == 1


def test_duplicates_inside_one_list_resolved_deterministically(ctx):
    res = run(ctx, [raw(20, title="Data Engineer"), raw(3, title="Data Engineer"),
                    raw(100, title="Data Engineer")], T0)
    js = jobs(ctx)
    # same first_seen_at -> smallest external_id by string compare ("100") stays open
    assert js["100"].status == "open"
    assert js["20"].status == "duplicate" and js["3"].status == "duplicate"
    assert res.created == 3 and res.duplicates == 2
    assert company(ctx).open_jobs == 1


def test_repeated_external_id_in_list_keeps_last(ctx):
    res = run(ctx, [raw(1, desc="<p>first</p>"), raw(1, desc="<p>last</p>")], T0)
    assert res.created == 1
    assert jobs(ctx)["1"].description == "last"


def test_missing_duplicate_is_closed(ctx):
    run(ctx, [raw(1, title="X"), raw(2, title="X")], T0)
    res = run(ctx, [raw(1, title="X")], T1)
    js = jobs(ctx)
    assert js["2"].status == "closed" and js["2"].closed_at == T1 and res.closed == 1


def test_empty_result_with_many_open_raises_and_closes_nothing(ctx):
    run(ctx, [raw(i) for i in range(5)], T0)
    with pytest.raises(SuspiciousEmptyResult) as ei:
        run(ctx, [], T1)
    assert "vazia" in str(ei.value)
    js = jobs(ctx)
    assert all(j.status == "open" and j.last_seen_at == T0 for j in js.values())
    assert company(ctx).open_jobs == 5


def test_all_items_filtered_out_counts_as_empty_and_raises(ctx):
    run(ctx, [raw(i) for i in range(5)], T0)
    with pytest.raises(SuspiciousEmptyResult):
        run(ctx, [raw("x" * 300)], T1)
    assert all(j.status == "open" for j in jobs(ctx).values())


def test_empty_result_with_few_open_closes_them(ctx):
    run(ctx, [raw(i) for i in range(4)], T0)
    res = run(ctx, [], T1)
    assert res.closed == 4
    assert all(j.status == "closed" for j in jobs(ctx).values())
    assert company(ctx).open_jobs == 0


def test_open_jobs_counter_updated(ctx):
    run(ctx, [raw(1), raw(2), raw(3)], T0)
    assert company(ctx).open_jobs == 3
    run(ctx, [raw(1), raw(4)], T1)
    assert company(ctx).open_jobs == 2


def test_ingest_idempotent_twice(ctx):
    raws = [raw(1), raw(2), raw(3, title="Engineer 1")]
    first = run(ctx, raws, T0)
    assert first.created == 3 and first.duplicates == 1
    second = run(ctx, raws, T1)
    assert second == IngestResult(created=0, updated=0, unchanged=3, closed=0,
                                  reopened=0, duplicates=0)
    js = jobs(ctx)
    assert len(js) == 3
    assert {e: j.status for e, j in js.items()} == {
        "1": "open", "2": "open", "3": "duplicate"}


def test_other_company_jobs_untouched(ctx):
    uid, _ = ctx
    with SessionLocal() as s:
        other = Company(user_id=uid, source="greenhouse", identifier="other", name="O")
        s.add(other)
        s.flush()
        s.add(Job(user_id=uid, company_id=other.id, external_id="1", url="u", title="t",
                  description="d", raw={}, content_hash="h", fingerprint="f"))
        s.commit()
        oid = other.id
    run(ctx, [], T0)
    with SessionLocal() as s:
        assert s.scalars(select(Job).where(Job.company_id == oid)).one().status == "open"


def test_nul_in_raw_json_is_stripped_and_company_ingests(ctx):
    bad = RawJob(
        external_id="2", title="Eng\x00ineer", url="https://example.test/jobs/2",
        location_text="Remote", description_html="<p>ok</p>", department=None,
        posted_at=T0, raw={"id": 2, "content": "a\x00b", "nested": [{"k\x00": "v\x00"}]},
    )
    res = run(ctx, [raw(1), bad, raw(3)], T0)
    assert res.created == 3 and res.skipped == 0
    js = jobs(ctx)
    assert js["2"].raw == {"id": 2, "content": "ab", "nested": [{"k": "v"}]}
    assert js["2"].title == "Engineer"
    assert company(ctx).open_jobs == 3


def test_nul_in_description_is_stripped_and_company_ingests(ctx):
    res = run(ctx, [raw(1), raw(2, desc="<p>Build\x00 things.</p>", loc="Re\x00mote"), raw(3)],
              T0)
    assert res.created == 3
    js = jobs(ctx)
    assert "\x00" not in js["2"].description and "Build" in js["2"].description
    assert js["2"].location_text == "Remote"


def test_salary_too_large_for_column_is_unknown_and_company_ingests(ctx):
    res = run(ctx, [raw(1), raw(2, desc="<p>Salary: $999,999,999,999k per year</p>"), raw(3)],
              T0)
    assert res.created == 3
    j = jobs(ctx)["2"]
    assert j.salary_known is False and j.salary_min is None and j.salary_max is None


def test_one_unsavable_job_is_skipped_and_the_rest_ingest(ctx, monkeypatch, caplog):
    """A job the DB rejects (DataError) is skipped in its own savepoint; the
    company and every other job still ingest, and a later run retries it."""
    from decimal import Decimal

    from rumo_core.sources import ingest as ingest_mod

    real = ingest_mod.normalize_job

    def broken(r):
        n = real(r)
        if r.external_id == "2":  # bypasses the extractor's guard on purpose
            return dataclasses.replace(n, salary_min=Decimal("1e15"), salary_known=True)
        return n

    run(ctx, [raw(1), raw(2), raw(5)], T0)  # job 2 exists and is open
    monkeypatch.setattr(ingest_mod, "normalize_job", broken)
    with caplog.at_level("WARNING", logger="rumo_core.sources.ingest"):
        res = run(ctx, [raw(1, desc="<p>changed</p>"), raw(2, desc="<p>new 2</p>"), raw(3),
                        raw(4)], T1)
    assert res.skipped == 1
    assert res.created == 2 and res.updated == 1 and res.closed == 1  # 5 closed
    js = jobs(ctx)
    # the skipped job keeps its previous row (not closed: it is still on the board)
    assert js["2"].status == "open" and js["2"].last_seen_at == T0
    assert js["2"].description == "Build backend services in Python."
    assert js["1"].description == "changed" and js["3"].status == "open"
    assert js["5"].status == "closed"
    assert company(ctx).open_jobs == 4
    assert any("skipping job" in r.getMessage() for r in caplog.records)
    # a NEW unsavable job is simply not created
    res = run(ctx, [raw(1, desc="<p>changed</p>"), raw(2, desc="<p>new 2</p>"), raw(3),
                    raw(4)], T2)
    assert res.skipped == 1 and res.unchanged == 3


def test_new_unsavable_job_is_not_created(ctx, monkeypatch):
    from decimal import Decimal

    from rumo_core.sources import ingest as ingest_mod

    real = ingest_mod.normalize_job
    monkeypatch.setattr(
        ingest_mod, "normalize_job",
        lambda r: dataclasses.replace(real(r), salary_max=Decimal("1e15"))
        if r.external_id == "2" else real(r),
    )
    res = run(ctx, [raw(1), raw(2), raw(3)], T0)
    assert res.created == 2 and res.skipped == 1
    assert set(jobs(ctx)) == {"1", "3"}
    assert company(ctx).open_jobs == 2


def test_bulk_ingest_is_fast_and_query_bounded(ctx):
    raws = [raw(i, desc=f"<p>Job number {i}. Python, Postgres.</p>") for i in range(1500)]
    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        start = time.perf_counter()
        res = run(ctx, raws, T0)
        first_elapsed = time.perf_counter() - start
        first_count = len(statements)
        statements.clear()
        start = time.perf_counter()
        res2 = run(ctx, raws[:1400] + [raw(i, desc="<p>changed</p>") for i in range(1400, 1500)],
                   T1)
        second_elapsed = time.perf_counter() - start
        second_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert res.created == 1500
    assert res2.unchanged == 1400 and res2.updated == 100
    assert first_elapsed < 5 and second_elapsed < 5
    assert first_count <= 20, first_count
    assert second_count <= 20, second_count
