"""Portal routes: the compliance verdict of each site and the user's on/off choice.

Only conditional portals can be switched on or off. `blocked` sites never open,
and a site that refused us stays suspended until the user clears it by hand.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.db import get_session
from rumo_core.models import PortalState
from rumo_core.sources.portals.gate import load_policy
from rumo_core.sources.portals.state import set_enabled

router = APIRouter(tags=["portals"])

MSG_UNKNOWN = "Portal desconhecido."
MSG_NOT_TOGGLABLE = "Só portais condicionais podem ser ligados ou desligados."


class PortalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: str
    status: str
    checked_at: date
    evidence_urls: list[str]
    evidence_summary: str
    enabled: bool
    blocked_by_site: bool
    blocked_at: datetime | None
    can_toggle: bool


class PortalToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def _out(entry, row: PortalState | None) -> PortalOut:
    return PortalOut(
        id=entry.id, name=entry.name, kind=entry.kind, status=entry.status,
        checked_at=entry.checked_at, evidence_urls=list(entry.evidence_urls),
        evidence_summary=entry.evidence_summary,
        enabled=bool(row and row.enabled), blocked_by_site=bool(row and row.blocked_by_site),
        blocked_at=row.blocked_at if row else None,
        can_toggle=entry.status == "conditional",
    )


@router.get("/portals", response_model=list[PortalOut])
def list_portals(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    rows = {
        r.portal: r for r in session.scalars(
            select(PortalState).where(PortalState.user_id == user_id)
        )
    }
    return [_out(entry, rows.get(entry.id)) for entry in load_policy().values()]


@router.put("/portals/{portal_id}", response_model=PortalOut)
def toggle_portal(
    portal_id: str,
    body: PortalToggle,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    entry = load_policy().get(portal_id)
    if entry is None:
        raise HTTPException(404, MSG_UNKNOWN)
    if entry.status != "conditional":
        raise HTTPException(409, MSG_NOT_TOGGLABLE)
    row = set_enabled(session, user_id, portal_id, body.enabled)
    session.commit()
    session.refresh(row)
    return _out(entry, row)
