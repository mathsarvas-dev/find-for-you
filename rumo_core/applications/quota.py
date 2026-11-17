"""Daily cap on resume generations, shared by the worker and the API (which does
not import worker.*). Counts all users' resume documents in the last 24 h."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rumo_core.models import ApplicationDocument


def generated_last_24h(session: Session) -> int:
    return session.scalar(
        select(func.count(ApplicationDocument.id)).where(
            ApplicationDocument.kind == "resume",
            ApplicationDocument.created_at > func.now() - timedelta(hours=24),
        )
    ) or 0
