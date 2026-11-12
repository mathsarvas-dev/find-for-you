"""Per-user Postgres advisory locks (transaction level, released at commit/rollback).

Lock ordering (cannot deadlock): a path may take the suggestion row lock
FIRST and then the per-user profile lock; nothing takes them in the opposite
order. Advisory xact locks are re-entrant within one transaction, so
confirm_profile can take the same lock its caller already holds.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

NS_PROFILE = 7101
NS_INTERVIEW_START = 7102


def _lock(session: Session, namespace: int, user_id: int) -> None:
    session.execute(select(func.pg_advisory_xact_lock(namespace, user_id)))


def lock_user_profile(session: Session, user_id: int) -> None:
    """Serialise every profile write (new version) of this user."""
    _lock(session, NS_PROFILE, user_id)


def lock_user_interview_start(session: Session, user_id: int) -> None:
    _lock(session, NS_INTERVIEW_START, user_id)
