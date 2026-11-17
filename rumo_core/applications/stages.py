"""Pipeline stages (marco 6) on top of the marco 5 application row.

The stage is the person's view of where a job stands; applied_at is the date
of the application and state stays the kit's own state. Moving a card keeps
the two consistent without duplicating either.
"""

from datetime import UTC, datetime

STAGES = ("saved", "preparing", "applied", "interview", "offer", "rejected", "no_response")
# stages after the application was sent
APPLIED_STAGES = frozenset({"applied", "interview", "offer", "rejected", "no_response"})
# a reply came back (a rejection is a reply; silence is not)
ANSWERED_STAGES = frozenset({"interview", "offer", "rejected"})
# nothing left to follow up
CLOSED_STAGES = frozenset({"rejected", "no_response"})


def move_stage(app, stage: str) -> None:
    """Sets the stage and keeps applied_at and the kit state consistent."""
    app.stage = stage
    if stage in APPLIED_STAGES:
        if app.applied_at is None:
            app.applied_at = datetime.now(UTC)
        if app.state == "ready":
            app.state = "applied"
    else:
        app.applied_at = None
        if app.state == "applied":
            app.state = "ready"
