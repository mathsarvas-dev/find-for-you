"""Stubs and gated jobs stay away from embedding, filling, ranking and lists."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.main import app, current_user_id
from api.overview import _jobs
from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.ranking import VISIBLE_INTAKES, _base_jobs
from rumo_core.models import Company, Job, User
from worker.collect_jobs import _select_fill_candidates, pending_jobs_stmt

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 16, tzinfo=UTC)


@pytest.fixture
def ctx():
    with SessionLocal() as s:
        u = User(email=f"funnel-consumers-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
        s.add(c)
        s.flush()
        uid, cid = u.id, c.id
        s.add_all([
            _row(uid, cid, "full", "full"),
            _row(uid, cid, "stub", "stub"),
            _row(uid, cid, "gated", "gated"),
        ])
        s.commit()
    yield uid, cid
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == uid))
        s.execute(delete(Company).where(Company.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _row(uid, cid, ext, intake):
    stub = intake == "stub"
    return Job(
        user_id=uid, company_id=cid, external_id=ext, url=f"https://example.test/{ext}",
        title=f"Job {ext}", description="" if stub else "texto", raw={} if stub else {"k": 1},
        content_hash=f"h{ext}", fingerprint=f"f{ext}", intake=intake,
        llm_filled_at=NOW if stub else None, work_model="unknown",
        stub_reason="work_model" if stub else None,
    )


def _ids(stmt, session):
    return {j.external_id for j, _ in session.execute(stmt).all()}


def test_stub_not_embedded_gated_still_is(ctx):
    uid, _ = ctx
    with SessionLocal() as s:
        ids = _ids(pending_jobs_stmt("m1", uid), s)
    assert ids == {"full", "gated"}


def test_stub_and_gated_not_filled(ctx):
    uid, _ = ctx
    picked, _ = _select_fill_candidates(SessionLocal, batch=10, user_id=uid)
    with SessionLocal() as s:
        external = {s.get(Job, jid).external_id for jid, _ in picked}
    assert "gated" not in external and "stub" not in external
    assert external <= {"full"}


def test_base_jobs_visible_intakes(ctx):
    uid, _ = ctx
    assert VISIBLE_INTAKES == ("full", "gated")
    with SessionLocal() as s:
        stmt = _base_jobs(uid, SearchFilters(), "open", select(Job.external_id))
        assert {r[0] for r in s.execute(stmt).all()} == {"full", "gated"}


def test_stub_detail_404_and_list_hides_stubs(ctx):
    uid, cid = ctx
    with SessionLocal() as s:
        stub_id = s.scalars(select(Job.id).where(Job.user_id == uid, Job.external_id == "stub")).one()
    app.dependency_overrides[current_user_id] = lambda: uid
    try:
        client = TestClient(app)
        listed = client.get("/jobs?status=all").json()
        assert {it["title"] for it in listed["items"]} == {"Job full", "Job gated"}
        assert client.get(f"/jobs/{stub_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_overview_outside_filters_counts_open_stubs(ctx):
    uid, _ = ctx
    with SessionLocal() as s:
        overview = _jobs(s, uid)
    assert overview.open == 2
    assert overview.outside_filters == 1
