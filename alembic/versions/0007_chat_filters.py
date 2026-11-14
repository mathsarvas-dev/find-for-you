"""chat filters: job labels, query model version, quality seed, unaccent

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.add_column(
        "search_profiles",
        sa.Column("query_model_version", sa.String(128)),
    )
    op.add_column(
        "user_preferences", sa.Column("quality_seed", sa.Integer())
    )
    op.create_table(
        "job_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id", sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("label", sa.String(8), nullable=False),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("user_id", "job_id", name="uq_job_labels_user_job"),
        sa.CheckConstraint(
            "label in ('good','bad')", name="ck_job_labels_label"
        ),
    )
    op.create_index("ix_job_labels_user_id", "job_labels", ["user_id"])


def downgrade() -> None:
    op.drop_table("job_labels")
    op.drop_column("user_preferences", "quality_seed")
    op.drop_column("search_profiles", "query_model_version")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
