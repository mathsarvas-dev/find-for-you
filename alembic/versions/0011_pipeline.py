"""pipeline: stage, follow-up date and notes on applications

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

STAGES = "('saved','preparing','applied','interview','offer','rejected','no_response')"


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("stage", sa.String(16), nullable=False, server_default="saved"),
    )
    op.add_column("applications", sa.Column("follow_up_at", sa.Date()))
    op.add_column("applications", sa.Column("notes", sa.Text()))
    # rows made by marco 5 keep their meaning
    op.execute("UPDATE applications SET stage = 'applied' WHERE state = 'applied'")
    op.execute(
        "UPDATE applications SET stage = 'preparing' "
        "WHERE state IN ('generating','ready','error')"
    )
    op.create_check_constraint("ck_applications_stage", "applications", f"stage in {STAGES}")
    op.create_index("ix_applications_user_stage", "applications", ["user_id", "stage"])


def downgrade() -> None:
    op.drop_index("ix_applications_user_stage", table_name="applications")
    op.drop_constraint("ck_applications_stage", "applications", type_="check")
    op.drop_column("applications", "notes")
    op.drop_column("applications", "follow_up_at")
    op.drop_column("applications", "stage")
