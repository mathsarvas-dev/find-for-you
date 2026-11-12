"""profile: imports, versioned profiles, embedded chunks

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
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
        "profile_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("raw_text", sa.Text()),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("extracted", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "truncated", sa.Boolean(), nullable=False, server_default="false"
        ),
        _user_id(),
        *_ts(),
    )
    op.create_index("ix_profile_imports_user_id", "profile_imports", ["user_id"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("narrative", sa.Text()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint("user_id", "version", name="uq_profiles_user_version"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])
    op.create_index(
        "ix_profiles_user_content_hash", "profiles", ["user_id", "content_hash"]
    )

    op.create_table(
        "profile_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(1024)),
        sa.Column("model_version", sa.String(128)),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint(
            "user_id", "profile_version", "source_ref",
            name="uq_profile_chunks_user_version_source",
        ),
    )
    op.create_index("ix_profile_chunks_user_id", "profile_chunks", ["user_id"])
    op.create_index(
        "ix_profile_chunks_user_model_version",
        "profile_chunks", ["user_id", "model_version"],
    )


def downgrade() -> None:
    op.drop_table("profile_chunks")
    op.drop_table("profiles")
    op.drop_table("profile_imports")
