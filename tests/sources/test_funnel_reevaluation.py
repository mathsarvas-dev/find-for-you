import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters
from rumo_core.models import Company, Job, JobFeed, SearchProfile, User
from rumo_core.sources.funnel import mark_for_reevaluation

pytestmark = pytest.mark.db
PAST = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def ctx():
    with SessionLocal() as s:
        u = User(email=f"reeval-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        with_stub = Company(user_id=u.id, source="greenhouse", identifier="a", name="A",
                            next_run_at=PAST)
        no_stub = Company(user_id=u.id, source="greenhouse", identifier="b", name="B",
                          next_run_at=PAST)
        all_mode = Company(user_id=u.id, source="greenhouse", identifier="c", name="C",
                           collect_mode="all", next_run_at=PAST)
        s.add_all([with_stub, no_stub, all_mode])
        s.flush()
        s.add(Job(user_id=u.id, company_id=with_stub.id, external_id="1", url="https://x/1",
                  title="t", description="", raw={}, content_hash="h1", fingerprint="f1",
                  intake="stub", stub_reason="work_model"))
        s.add(Job(user_id=u.id, company_id=all_mode.id, external_id="2", url="https://x/2",
                  title="t", description="", raw={}, content_hash="h2", fingerprint="f2",
                  intake="stub", stub_reason="work_model"))
        s.add(JobFeed(user_id=u.id, source="remotive", params={}, next_run_at=PAST))
        s.commit()
        ids = {"uid": u.id, "with": with_stub.id, "none": no_stub.id, "all": all_mode.id}
    yield ids
    with SessionLocal() as s:
        s.execute(delete(JobFeed).where(JobFeed.user_id == ids["uid"]))
        s.execute(delete(Job).where(Job.user_id == ids["uid"]))
        s.execute(delete(Company).where(Company.user_id == ids["uid"]))
        s.execute(delete(SearchProfile).where(SearchProfile.user_id == ids["uid"]))
        s.execute(delete(User).where(User.id == ids["uid"]))
        s.commit()


def test_filter_change_marks_companies_with_stubs(ctx):
    with SessionLocal() as s:
        companies, _ = mark_for_reevaluation(s, ctx["uid"])
        s.commit()
    assert companies == 1
    with SessionLocal() as s:
        assert s.get(Company, ctx["with"]).next_run_at > PAST
        assert s.get(Company, ctx["none"]).next_run_at == PAST
        assert s.get(Company, ctx["all"]).next_run_at == PAST


def test_feed_with_stubs_is_marked_only_when_it_has_stubs(ctx):
    with SessionLocal() as s:
        _, feeds = mark_for_reevaluation(s, ctx["uid"])
        s.commit()
    assert feeds == 0  # no stub from a feed company yet
    with SessionLocal() as s:
        feed = s.scalars(select(JobFeed).where(JobFeed.user_id == ctx["uid"])).one()
        assert feed.next_run_at == PAST


def test_put_search_profile_triggers_reevaluation(ctx):
    from fastapi.testclient import TestClient

    from api.main import app, current_user_id

    app.dependency_overrides[current_user_id] = lambda: ctx["uid"]
    try:
        client = TestClient(app)
        r = client.put("/search-profile", json={"work_models": ["onsite"], "include_unknown": False})
        assert r.status_code == 200, r.text
        with SessionLocal() as s:
            assert s.get(Company, ctx["with"]).next_run_at > PAST
        with SessionLocal() as s:
            s.get(Company, ctx["with"]).next_run_at = PAST
            s.commit()
        r = client.put("/search-profile", json={"work_models": ["onsite"], "include_unknown": False})
        assert r.status_code == 200
        with SessionLocal() as s:
            assert s.get(Company, ctx["with"]).next_run_at == PAST
    finally:
        app.dependency_overrides.clear()
