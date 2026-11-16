import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.discovery.names import employer_key
from rumo_core.models import Company, Job, User
from rumo_core.sources.base import RawJob
from rumo_core.sources.ingest import IngestPolicy, ingest_jobs

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)
T2 = T1 + timedelta(hours=6)
ATS = IngestPolicy(origin="ats", source_rank=0)
FEED = IngestPolicy(origin="feed", source_rank=2, close_missing=False)


def raw(eid, title="Backend Engineer", loc="Remote, Brazil"):
    return RawJob(
        external_id=str(eid), title=title, url=f"https://example.test/jobs/{eid}",
        location_text=loc, description_html="<p>Build backend services in Python.</p>",
        department="Engineering", posted_at=T0, raw={"id": eid},
    )


@pytest.fixture
def ctx():
    with SessionLocal() as s:
        u = User(email=f"ingest-policy-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        acme_ats = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
        acme_feed = Company(user_id=u.id, source="remotive", identifier="acme-feed",
                            name="ACME", origin="feed", active=False)
        other = Company(user_id=u.id, source="lever", identifier="zeta", name="Zeta")
        s.add_all([acme_ats, acme_feed, other])
        s.commit()
        ids = {"uid": u.id, "ats": acme_ats.id, "feed": acme_feed.id, "other": other.id}
    yield ids
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == ids["uid"]))
        s.execute(delete(Company).where(Company.user_id == ids["uid"]))
        s.execute(delete(User).where(User.id == ids["uid"]))
        s.commit()


def run(ctx, company_key, raws, now, policy):
    with SessionLocal() as s:
        company = s.get(Company, ctx[company_key])
        res = ingest_jobs(s, ctx["uid"], company, raws, now=now, policy=policy)
        s.commit()
        return res


def jobs_of(ctx, company_key):
    with SessionLocal() as s:
        return {j.external_id: (j.status, j.origin, j.source_rank) for j in s.scalars(
            select(Job).where(Job.user_id == ctx["uid"], Job.company_id == ctx[company_key])
        )}


def test_employer_key():
    assert employer_key("Acme Ltda.") == "acme"
    assert employer_key("ACME") == "acme"
    assert employer_key("Acme Inc") == "acme"
    assert employer_key("Ltda") is None
    assert employer_key("") is None
    assert employer_key(None) is None


def test_policy_sets_origin_and_rank(ctx):
    run(ctx, "feed", [raw(1)], T0, FEED)
    assert jobs_of(ctx, "feed")["1"] == ("open", "feed", 2)


def test_close_missing_false_keeps_absent_open(ctx):
    run(ctx, "feed", [raw(1), raw(2, title="Data Engineer")], T0, FEED)
    res = run(ctx, "feed", [raw(1)], T1, FEED)
    assert res.closed == 0
    assert jobs_of(ctx, "feed")["2"][0] == "open"


def test_empty_feed_fetch_closes_nothing(ctx):
    run(ctx, "feed", [raw(1)], T0, FEED)
    run(ctx, "feed", [], T1, FEED)
    assert jobs_of(ctx, "feed")["1"][0] == "open"


def test_listed_ids_keep_jobs_open(ctx):
    run(ctx, "ats", [raw(1), raw(2, title="Data Engineer")], T0, ATS)
    listed = IngestPolicy(origin="site", source_rank=1, listed_ids=frozenset({"2"}))
    run(ctx, "ats", [raw(1)], T1, listed)
    assert jobs_of(ctx, "ats")["2"][0] == "open"


def test_ats_outranks_feed_same_employer(ctx):
    run(ctx, "feed", [raw(1, title="Backend Engineer", loc="Remote, Brazil")], T0, FEED)
    assert jobs_of(ctx, "feed")["1"][0] == "open"
    run(ctx, "ats", [raw("a1", title="Backend Engineer", loc="Remote, Brazil")], T1, ATS)
    assert jobs_of(ctx, "ats")["a1"][0] == "open"
    assert jobs_of(ctx, "feed")["1"][0] == "duplicate"
    # the feed fetch again keeps the feed copy as duplicate
    run(ctx, "feed", [raw(1, title="Backend Engineer", loc="Remote, Brazil")], T2, FEED)
    assert jobs_of(ctx, "feed")["1"][0] == "duplicate"
    with SessionLocal() as s:
        assert s.get(Company, ctx["feed"]).open_jobs == 0


def test_feed_promoted_when_ats_job_closes(ctx):
    run(ctx, "feed", [raw(1)], T0, FEED)
    run(ctx, "ats", [raw("a1")], T0, ATS)
    assert jobs_of(ctx, "feed")["1"][0] == "duplicate"
    run(ctx, "ats", [], T1, ATS)  # the ATS board empties (guard only if many open)
    run(ctx, "feed", [raw(1)], T2, FEED)
    assert jobs_of(ctx, "feed")["1"][0] == "open"


def test_same_fingerprint_other_employer_not_deduped(ctx):
    run(ctx, "other", [raw("z1", title="Backend Engineer")], T0, ATS)
    run(ctx, "ats", [raw("a1", title="Backend Engineer")], T0, ATS)
    assert jobs_of(ctx, "other")["z1"][0] == "open"
    assert jobs_of(ctx, "ats")["a1"][0] == "open"


def test_equal_rank_not_deduped(ctx):
    run(ctx, "ats", [raw("a1")], T0, ATS)
    other_ats = IngestPolicy(origin="ats", source_rank=0)
    run(ctx, "feed", [raw(1)], T0, other_ats)
    assert jobs_of(ctx, "feed")["1"][0] == "open"
    assert jobs_of(ctx, "ats")["a1"][0] == "open"
