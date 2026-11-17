"""Worker liveness: the worker upserts one row, the API reports it as down when it is stale."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from rumo_core.models import WorkerHeartbeat

WORKER_NAME = "worker"
STALE_AFTER = timedelta(minutes=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_heartbeat(session_factory, now: datetime | None = None) -> None:
    now = now or _now()
    stmt = insert(WorkerHeartbeat).values(name=WORKER_NAME, seen_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[WorkerHeartbeat.name], set_={"seen_at": now}
    )
    with session_factory() as s:
        s.execute(stmt)
        s.commit()


def worker_status(session, now: datetime | None = None) -> dict:
    """{"status": "up" | "down", "seen_at": ISO or None}. Never seen = down."""
    now = now or _now()
    seen = session.scalar(
        select(WorkerHeartbeat.seen_at).where(WorkerHeartbeat.name == WORKER_NAME)
    )
    up = seen is not None and now - seen <= STALE_AFTER
    return {
        "status": "up" if up else "down",
        "seen_at": seen.isoformat() if seen is not None else None,
    }
