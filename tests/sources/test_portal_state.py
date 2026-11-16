import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from rumo_core.db import SessionLocal
from rumo_core.models import PortalState, User
from rumo_core.sources.portals.gate import PortalUserState
from rumo_core.sources.portals.state import load_states, mark_blocked_by_site, set_enabled

pytestmark = pytest.mark.db


@pytest.fixture
def users():
    with SessionLocal() as s:
        a = User(email=f"portal-a-{uuid.uuid4().hex}@example.test")
        b = User(email=f"portal-b-{uuid.uuid4().hex}@example.test")
        s.add_all([a, b])
        s.commit()
        ids = [a.id, b.id]
        yield s, ids
        s.rollback()
        s.execute(delete(PortalState).where(PortalState.user_id.in_(ids)))
        s.execute(delete(User).where(User.id.in_(ids)))
        s.commit()


def test_portal_state_roundtrip(users):
    s, (a, b) = users
    set_enabled(s, a, "vagas", True)
    set_enabled(s, a, "vagas", True)
    s.commit()
    assert s.query(PortalState).filter_by(user_id=a, portal="vagas").count() == 1

    mark_blocked_by_site(s, a, "vagas", datetime(2026, 11, 14, tzinfo=timezone.utc))
    s.commit()
    states = load_states(s, a)
    assert states["vagas"] == PortalUserState(enabled=True, blocked_by_site=True)

    set_enabled(s, b, "catho", False)
    s.commit()
    assert set(load_states(s, a)) == {"vagas"}
    assert load_states(s, b) == {"catho": PortalUserState(enabled=False, blocked_by_site=False)}
