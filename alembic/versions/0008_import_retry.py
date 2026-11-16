"""import retry queue (retry_at) and provisional profiles

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_imports", sa.Column("retry_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "profiles",
        sa.Column(
            "provisional", sa.Boolean(), nullable=False, server_default="false"
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "provisional")
    op.drop_column("profile_imports", "retry_at")
