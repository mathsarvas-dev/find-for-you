import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters
from rumo_core.models import Company, Job, User
from rumo_core.sources.base import RawJob
from rumo_core.sources.ingest import IngestPolicy, ingest_jobs

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)
REMOTE = SearchFilters(work_models=["remote"], include_unknown=False)
ONSITE_ONLY = SearchFilters(work_models=["onsite"], include_unknown=False)


def raw(eid, loc="Remote, Brazil", desc="<p>Build backend services in Python.</p>"):
    return RawJob(
        external_id=str(eid), title=f"Engineer {eid}",
        url=f"https://example.test/jobs/{eid}", location_text=loc,
        description_html=desc, department="Engineering", posted_at=T0, raw={"id": eid},
    )


@pytest.fixture
def ctx():
    with SessionLocal() as s:
        u = User(email=f"funnel-ingest-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
        s.add(c)
        s.commit()
        ids = (u.id, c.id)
    yield ids
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == ids[0]))
        s.execute(delete(Company).where(Company.user_id == ids[0]))
        s.execute(delete(User).where(User.id == ids[0]))
        s.commit()


def run(ctx, raws, now, filters):
    uid, cid = ctx
    with SessionLocal() as s:
        res = ingest_jobs(s, uid, s.get(Company, cid), raws, now=now,
                          policy=IngestPolicy(filters=filters))
        s.commit()
        return res


def job(ctx, eid):
    with SessionLocal() as s:
        j = s.scalars(select(Job).where(Job.user_id == ctx[0], Job.external_id == str(eid))).one()
        s.expunge(j)
        return j


def test_job_failing_filters_becomes_stub(ctx):
    res = run(ctx, [raw(1, loc="Berlin, Germany")], T0, ONSITE_ONLY)
    j = job(ctx, 1)
    assert j.intake == "stub"
    assert j.description == "" and j.raw == {}
    assert j.embedding is None and j.llm_filled_at is not None
    assert j.stub_reason == "work_model"
    assert j.status == "open"
    assert res.stubs == 1
    with SessionLocal() as s:
        assert s.get(Company, ctx[1]).open_jobs == 0


def test_recollect_promotes_stub_after_filter_change(ctx):
    run(ctx, [raw(1)], T0, ONSITE_ONLY)  # Remote job, onsite filter: stub
    assert job(ctx, 1).intake == "stub"
    res = run(ctx, [raw(1)], T1, None)  # filters removed
    j = job(ctx, 1)
    assert j.intake == "full" and j.stub_reason is None
    assert j.description.startswith("Build backend")
    assert j.llm_filled_at is None
    assert res.promoted == 1


def test_full_job_not_demoted_when_unchanged(ctx):
    run(ctx, [raw(1)], T0, None)
    run(ctx, [raw(1)], T1, ONSITE_ONLY)
    assert job(ctx, 1).intake == "full"


def test_changed_content_reevaluated_to_stub(ctx):
    run(ctx, [raw(1)], T0, None)
    run(ctx, [raw(1, loc="Berlin, Germany")], T1, ONSITE_ONLY)
    j = job(ctx, 1)
    assert j.intake == "stub" and j.stub_reason == "work_model"


def test_stub_not_counted_in_open_jobs(ctx):
    run(ctx, [raw(1), raw(2, loc="Berlin, Germany")], T0, REMOTE)
    assert job(ctx, 1).intake == "full"
    assert job(ctx, 2).intake == "stub"
    with SessionLocal() as s:
        assert s.get(Company, ctx[1]).open_jobs == 1


def test_stub_absent_later_is_closed(ctx):
    run(ctx, [raw(1, loc="Berlin, Germany")], T0, ONSITE_ONLY)
    run(ctx, [], T1, ONSITE_ONLY)  # one open stub: guard does not apply (under 5 open)
    assert job(ctx, 1).status == "closed"


def test_keywords_make_feed_jobs_stubs(ctx):
    uid, cid = ctx
    with SessionLocal() as s:
        res = ingest_jobs(
            s, uid, s.get(Company, cid),
            [raw(1, desc="<p>Python e SQL</p>"), raw(2, desc="<p>Cozinha industrial</p>")],
            now=T0, policy=IngestPolicy(filters=SearchFilters(), keywords=("python",)),
        )
        s.commit()
    assert job(ctx, 1).intake == "full"
    assert job(ctx, 2).intake == "stub" and job(ctx, 2).stub_reason == "feed_keywords"
    assert res.stubs == 1


def test_collect_mode_all_keeps_everything_full(ctx):
    run(ctx, [raw(1, loc="Berlin, Germany")], T0, None)
    assert job(ctx, 1).intake == "full"
