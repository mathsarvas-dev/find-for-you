"""discovery: candidates, user preferences and company origin

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "origin", sa.String(16), nullable=False, server_default="manual"
        ),
    )
    op.create_check_constraint(
        "ck_companies_origin", "companies", "origin in ('manual','auto')"
    )

    op.create_table(
        "discovery_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("name_hint", sa.String(255)),
        sa.Column("origin_kind", sa.String(16), nullable=False),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "company_id", sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
        ),
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
        sa.UniqueConstraint(
            "user_id", "source", "token",
            name="uq_discovery_candidates_user_source_token",
        ),
        sa.CheckConstraint(
            "origin_kind in ('catalog','profile','target')",
            name="ck_discovery_candidates_origin_kind",
        ),
        sa.CheckConstraint(
            "state in ('pending','valid','invalid','known','rejected')",
            name="ck_discovery_candidates_state",
        ),
    )
    op.create_index(
        "ix_discovery_candidates_user_id", "discovery_candidates", ["user_id"]
    )
    op.create_index(
        "ix_discovery_candidates_user_state_retry", "discovery_candidates",
        ["user_id", "state", "retry_after"],
    )

    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            primary_key=True,
        ),
        sa.Column(
            "discovery_enabled", sa.Boolean(), nullable=False,
            server_default="true",
        ),
        sa.Column(
            "target_companies", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_table("discovery_candidates")
    op.drop_constraint("ck_companies_origin", "companies", type_="check")
    op.drop_column("companies", "origin")
