from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from rumo_core.db import SessionLocal
from rumo_core.heartbeat import WORKER_NAME, record_heartbeat, worker_status
from rumo_core.models import WorkerHeartbeat


class _FakeSession:
    def __init__(self, seen):
        self._seen = seen

    def scalar(self, _stmt):
        return self._seen


def test_status_down_when_never_seen():
    out = worker_status(_FakeSession(None))
    assert out == {"status": "down", "seen_at": None}


def test_status_up_within_two_minutes():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    seen = now - timedelta(seconds=119)
    out = worker_status(_FakeSession(seen), now=now)
    assert out["status"] == "up"
    assert out["seen_at"] == seen.isoformat()


def test_status_down_after_two_minutes():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    out = worker_status(_FakeSession(now - timedelta(seconds=121)), now=now)
    assert out["status"] == "down"


@pytest.mark.db
def test_record_heartbeat_upserts_one_row():
    with SessionLocal() as s:
        s.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.name == WORKER_NAME))
        s.commit()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 1, 2, tzinfo=timezone.utc)
    record_heartbeat(SessionLocal, now=old)
    record_heartbeat(SessionLocal, now=new)
    with SessionLocal() as s:
        rows = s.query(WorkerHeartbeat).filter(WorkerHeartbeat.name == WORKER_NAME).all()
        assert len(rows) == 1
        assert rows[0].seen_at == new
