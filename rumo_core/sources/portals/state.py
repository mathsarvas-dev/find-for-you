"""Per-user portal choices (on/off and 'the site refused us') in `portal_state`."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from rumo_core.models import PortalState
from rumo_core.sources.portals.gate import PortalUserState


def _row(session: Session, user_id: int, portal: str) -> PortalState | None:
    return session.scalar(
        select(PortalState).where(PortalState.user_id == user_id, PortalState.portal == portal)
    )


def load_states(session: Session, user_id: int) -> dict[str, PortalUserState]:
    rows = session.scalars(select(PortalState).where(PortalState.user_id == user_id))
    return {r.portal: PortalUserState(enabled=r.enabled, blocked_by_site=r.blocked_by_site) for r in rows}


def set_enabled(session: Session, user_id: int, portal: str, enabled: bool) -> PortalState:
    row = _row(session, user_id, portal)
    if row is None:
        row = PortalState(user_id=user_id, portal=portal, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
    session.flush()
    return row


def mark_blocked_by_site(session: Session, user_id: int, portal: str, now: datetime) -> None:
    row = _row(session, user_id, portal)
    if row is None:
        row = PortalState(user_id=user_id, portal=portal)
        session.add(row)
    row.blocked_by_site = True
    row.blocked_at = now
    session.flush()
