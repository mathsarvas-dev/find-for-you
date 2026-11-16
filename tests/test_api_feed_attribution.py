import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.main import app, current_user_id
from rumo_core.db import SessionLocal
from rumo_core.models import Company, DiscoveryCandidate, Job, User
from rumo_core.sources.base import RawJob
from rumo_core.sources.feed_ingest import ingest_feed_items
from rumo_core.sources.feeds.base import FeedItem

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def uid():
    with SessionLocal() as s:
        u = User(email=f"api-attr-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        user_id = u.id
    app.dependency_overrides[current_user_id] = lambda: user_id
    yield user_id
    app.dependency_overrides.clear()
    with SessionLocal() as s:
        s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == user_id))
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(delete(Company).where(Company.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


def test_feed_job_carries_attribution_and_ats_job_does_not(uid):
    raw = RawJob(
        external_id="r1", title="Dev Python", url="https://remotive.com/jobs/r1",
        location_text="Remote", description_html="<p>Python.</p>", department=None,
        posted_at=None, raw={"id": "r1"}, work_model_hint="remote",
    )
    with SessionLocal() as s:
        ingest_feed_items(s, uid, "remotive", [FeedItem(raw=raw, employer_name="Acme Ltda")],
                          now=NOW, filters=None, keywords=())
        s.commit()
    client = TestClient(app)
    items = client.get("/jobs").json()["items"]
    assert len(items) == 1
    assert items[0]["origin"] == "feed"
    assert items[0]["attribution"] == {
        "label": "Vagas via Remotive", "url": "https://remotive.com",
    }
    detail = client.get(f"/jobs/{items[0]['id']}").json()
    assert detail["attribution"]["label"] == "Vagas via Remotive"


def test_ats_job_has_no_attribution(uid):
    with SessionLocal() as s:
        company = Company(user_id=uid, source="greenhouse", identifier="acme", name="Acme",
                          active=True, origin="auto")
        s.add(company)
        s.flush()
        s.add(Job(
            user_id=uid, company_id=company.id, external_id="a1", title="Dev", url="https://x.test/a1",
            location_text="Remote", description="d", raw={}, status="open", intake="full",
            origin="ats", source_rank=0, first_seen_at=NOW, last_seen_at=NOW, fill_attempts=0,
            content_hash="h", fingerprint="f", work_model="remote", seniority="unknown",
            language="unknown", salary_known=False,
        ))
        s.commit()
    items = TestClient(app).get("/jobs").json()["items"]
    assert items and items[0]["attribution"] is None
