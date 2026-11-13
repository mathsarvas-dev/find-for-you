"""matching: search_profiles and matches

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    ]


def _user_id() -> sa.Column:
    return sa.Column(
        "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "filters", JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("query_text", sa.Text()),
        sa.Column("query_embedding", Vector(1024)),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default="false"
        ),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint(
            "user_id", "name", name="uq_search_profiles_user_name"
        ),
    )
    op.create_index("ix_search_profiles_user_id", "search_profiles", ["user_id"])
    op.create_index(
        "uq_search_profiles_one_default", "search_profiles", ["user_id"],
        unique=True, postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id", sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("vec_score", sa.Float(), nullable=False),
        sa.Column("ai_score", sa.Integer()),
        sa.Column("rationale", sa.Text()),
        sa.Column("strengths", JSONB()),
        sa.Column("gaps", JSONB()),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default="pendente"
        ),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint(
            "user_id", "job_id", "profile_version",
            name="uq_matches_user_job_version",
        ),
        sa.CheckConstraint(
            "state in ('pendente','ok','erro')", name="ck_matches_state"
        ),
        sa.CheckConstraint(
            "ai_score between 0 and 100", name="ck_matches_ai_score"
        ),
    )
    op.create_index("ix_matches_user_id", "matches", ["user_id"])
    op.create_index(
        "ix_matches_user_version_score", "matches",
        ["user_id", "profile_version", sa.text("ai_score DESC")],
    )


def downgrade() -> None:
    op.drop_table("matches")
    op.drop_table("search_profiles")
