import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.embeddings.bge_m3 import MODEL_VERSION
from rumo_core.models import Application, Company, Job, Match, Profile, ProfileChunk, User
from rumo_core.profile.schema import Basics, CanonicalProfile

DIM = 1024

pytestmark = pytest.mark.db


@pytest.fixture
def env():
    created: list[int] = []

    def new_user(*, provisional=False, source="greenhouse"):
        with SessionLocal() as s:
            u = User(email=f"api-pipe-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            s.add(Company(user_id=u.id, source=source, identifier=f"acme{u.id}", name="Acme"))
            s.add(Profile(
                user_id=u.id, version=1, provisional=provisional, content_hash="p",
                data=CanonicalProfile(basics=Basics(name="Ana")).model_dump(mode="json"),
            ))
            v = [0.0] * DIM
            v[0] = 1.0
            s.add(ProfileChunk(
                user_id=u.id, profile_version=1, source_ref="exp1", kind="experience",
                text="t", content_hash="h", embedding=v, model_version=MODEL_VERSION,
            ))
            s.commit()
            return u.id

    def add_job(uid, *, score=None, **kw):
        with SessionLocal() as s:
            c = s.execute(select(Company).where(Company.user_id == uid)).scalars().one()
            fields = dict(
                user_id=uid, company_id=c.id, external_id=uuid.uuid4().hex[:8],
                url="https://example.test/j", title="Data Engineer", location_text="Remoto",
                description="d", raw={}, content_hash="h0", fingerprint=uuid.uuid4().hex,
                status="open", language="en", country="US",
            )
            fields.update(kw)
            j = Job(**fields)
            s.add(j)
            s.flush()
            if score is not None:
                s.add(Match(user_id=uid, job_id=j.id, profile_version=1, vec_score=0.5,
                            ai_score=score, state="ok", content_hash=fields["content_hash"]))
            s.commit()
            return j.id

    def add_app(uid, job_id, *, stage="saved", state="draft", **kw):
        with SessionLocal() as s:
            a = Application(user_id=uid, job_id=job_id, state=state, stage=stage,
                            locale="en", template="us", **kw)
            s.add(a)
            s.commit()
            return a.id

    def client_as(uid):
        app.dependency_overrides[current_user_id] = lambda: uid
        return TestClient(app)

    yield new_user, add_job, add_app, client_as
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        for uid in created:
            for m in (Application, Match, ProfileChunk, Profile, Job, Company):
                s.execute(delete(m).where(m.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


def _row(app_id):
    with SessionLocal() as s:
        return s.get(Application, app_id)


def test_new_application_is_saved_and_listed(env):
    new_user, add_job, _, client_as = env
    uid = new_user()
    jid = add_job(uid, score=85)
    c = client_as(uid)
    aid = c.post("/applications", json={"job_id": jid}).json()["id"]
    body = c.get("/pipeline").json()
    assert [(i["id"], i["stage"], i["ai_score"]) for i in body["items"]] == [(aid, "saved", 85)]
    assert body["items"][0]["job_title"] == "Data Engineer"
    assert body["items"][0]["company_name"] == "Acme"
    assert body["today"] == date.today().isoformat()


def test_pipeline_is_scoped_by_user(env):
    new_user, add_job, add_app, client_as = env
    a, b = new_user(), new_user()
    add_app(a, add_job(a))
    assert client_as(b).get("/pipeline").json()["items"] == []


def test_generate_moves_saved_to_preparing(env):
    new_user, add_job, _, client_as = env
    uid = new_user()
    c = client_as(uid)
    aid = c.post("/applications", json={"job_id": add_job(uid)}).json()["id"]
    assert c.post(f"/applications/{aid}/generate").status_code == 202
    assert _row(aid).stage == "preparing"


def test_apply_and_unapply_follow_the_stage(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    aid = add_app(uid, add_job(uid), stage="preparing", state="ready")
    c = client_as(uid)
    assert c.post(f"/applications/{aid}/apply").status_code == 200
    assert _row(aid).stage == "applied"
    assert c.post(f"/applications/{aid}/unapply").status_code == 200
    row = _row(aid)
    assert (row.stage, row.applied_at) == ("preparing", None)


def test_move_to_interview_sets_applied_at_and_state(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    aid = add_app(uid, add_job(uid), stage="preparing", state="ready")
    r = client_as(uid).patch(f"/pipeline/{aid}", json={"stage": "interview"})
    assert r.status_code == 200 and r.json()["stage"] == "interview"
    row = _row(aid)
    assert row.applied_at is not None and row.state == "applied"


def test_move_keeps_original_applied_at(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    when = datetime(2026, 1, 5, tzinfo=UTC)
    aid = add_app(uid, add_job(uid), stage="applied", state="applied", applied_at=when)
    client_as(uid).patch(f"/pipeline/{aid}", json={"stage": "offer"})
    assert _row(aid).applied_at == when


def test_move_back_before_applied_clears_applied_at(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    aid = add_app(uid, add_job(uid), stage="interview", state="applied",
                  applied_at=datetime.now(UTC))
    client_as(uid).patch(f"/pipeline/{aid}", json={"stage": "preparing"})
    row = _row(aid)
    assert (row.stage, row.applied_at, row.state) == ("preparing", None, "ready")


def test_move_to_applied_without_kit_leaves_state(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    aid = add_app(uid, add_job(uid))
    assert client_as(uid).patch(f"/pipeline/{aid}", json={"stage": "applied"}).status_code == 200
    row = _row(aid)
    assert (row.stage, row.state) == ("applied", "draft") and row.applied_at is not None


def test_move_to_applied_refused_with_provisional_profile(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user(provisional=True)
    aid = add_app(uid, add_job(uid))
    r = client_as(uid).patch(f"/pipeline/{aid}", json={"stage": "applied"})
    assert r.status_code == 409
    assert _row(aid).stage == "saved"


def test_invalid_stage_is_422_and_other_user_404(env):
    new_user, add_job, add_app, client_as = env
    owner, other = new_user(), new_user()
    aid = add_app(owner, add_job(owner))
    assert client_as(owner).patch(f"/pipeline/{aid}", json={"stage": "nope"}).status_code == 422
    assert client_as(other).patch(f"/pipeline/{aid}", json={"stage": "offer"}).status_code == 404


def test_follow_up_and_notes_set_and_clear(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    aid = add_app(uid, add_job(uid))
    c = client_as(uid)
    r = c.patch(f"/pipeline/{aid}", json={"follow_up_at": "2026-12-01", "notes": "  Falar com Ana  "})
    assert r.json()["follow_up_at"] == "2026-12-01" and r.json()["notes"] == "Falar com Ana"
    r = c.patch(f"/pipeline/{aid}", json={"follow_up_at": None})
    assert r.json()["follow_up_at"] is None and r.json()["notes"] == "Falar com Ana"
    r = c.patch(f"/pipeline/{aid}", json={"notes": "   "})
    assert r.json()["notes"] is None


def test_notes_too_long_is_422(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    aid = add_app(uid, add_job(uid))
    assert client_as(uid).patch(f"/pipeline/{aid}", json={"notes": "x" * 2001}).status_code == 422


def test_report_counts_and_rates(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    add_job(uid)  # found, no application
    j1 = add_job(uid, score=90, country="DE")
    j2 = add_job(uid, score=70, country="DE")
    j3 = add_job(uid, score=40, country="BR")
    j4 = add_job(uid, country=None)
    add_job(uid, intake="stub")  # outside the filters: not found
    now = datetime.now(UTC)
    add_app(uid, j1, stage="interview", state="applied", applied_at=now)
    add_app(uid, j2, stage="no_response", state="applied", applied_at=now)
    add_app(uid, j3, stage="rejected", state="applied", applied_at=now)
    add_app(uid, j4, stage="saved")
    r = client_as(uid).get("/pipeline/report").json()
    assert (r["found"], r["saved"], r["applied"], r["answered"]) == (5, 1, 3, 2)
    assert r["response_rate"] == pytest.approx(2 / 3)
    by_country = {g["key"]: g for g in r["by_country"]}
    assert (by_country["DE"]["applied"], by_country["DE"]["answered"]) == (2, 1)
    assert by_country["DE"]["rate"] == 0.5
    assert by_country["BR"]["rate"] == 1.0
    assert "unknown" not in by_country  # the saved one has not applied
    bands = {g["key"]: g for g in r["by_score_band"]}
    assert (bands["80_100"]["answered"], bands["60_79"]["answered"], bands["0_59"]["answered"]) == (1, 0, 1)
    assert [g["key"] for g in r["by_source"]] == ["greenhouse"]
    assert r["by_source"][0]["applied"] == 3


def test_report_empty_has_null_rate(env):
    new_user, _, _, client_as = env
    r = client_as(new_user()).get("/pipeline/report").json()
    assert (r["found"], r["applied"], r["response_rate"]) == (0, 0, None)
    assert r["by_country"] == []


def test_highlights_new_high_scores_only(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    good = add_job(uid, score=92, title="Great")
    add_job(uid, score=79, title="Low")
    add_job(uid, score=95, title="Old", first_seen_at=datetime.now(UTC) - timedelta(days=30))
    add_job(uid, score=95, title="Closed", status="closed")
    saved = add_job(uid, score=99, title="Saved")
    add_app(uid, saved)
    add_job(uid, title="Unscored")
    r = client_as(uid).get("/highlights").json()
    assert r["min_score"] == 80 and r["days"] == 7
    assert r["new_high_count"] == 1
    assert [(i["job_id"], i["ai_score"]) for i in r["items"]] == [(good, 92)]
    assert r["follow_ups_due"] == 0


def test_highlights_follow_ups_due(env):
    new_user, add_job, add_app, client_as = env
    uid = new_user()
    today = date.today()
    add_app(uid, add_job(uid), stage="applied", follow_up_at=today)
    add_app(uid, add_job(uid), stage="applied", follow_up_at=today - timedelta(days=3))
    add_app(uid, add_job(uid), stage="applied", follow_up_at=today + timedelta(days=3))
    add_app(uid, add_job(uid), stage="rejected", follow_up_at=today)  # closed: no reminder
    assert client_as(uid).get("/highlights").json()["follow_ups_due"] == 2
