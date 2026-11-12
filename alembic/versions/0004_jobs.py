"""jobs: companies and jobs

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
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


def _dt(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("country", sa.String(64)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        _dt("last_run_at"),
        _dt("last_success_at"),
        _dt("next_run_at"),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False,
            server_default="0",
        ),
        sa.Column("open_jobs", sa.Integer(), nullable=False, server_default="0"),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint(
            "user_id", "source", "identifier",
            name="uq_companies_user_source_identifier",
        ),
    )
    op.create_index("ix_companies_user_id", "companies", ["user_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id", sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("country", sa.String(64)),
        sa.Column("city", sa.String(255)),
        sa.Column("location_text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "work_model", sa.String(16), nullable=False, server_default="unknown"
        ),
        sa.Column(
            "seniority", sa.String(16), nullable=False, server_default="unknown"
        ),
        sa.Column(
            "language", sa.String(16), nullable=False, server_default="unknown"
        ),
        sa.Column("department", sa.String(255)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("raw", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        _dt("first_seen_at", server_default=sa.func.now(), nullable=False),
        _dt("last_seen_at", server_default=sa.func.now(), nullable=False),
        _dt("closed_at"),
        _dt("posted_at"),
        sa.Column("salary_min", sa.Numeric(14, 2)),
        sa.Column("salary_max", sa.Numeric(14, 2)),
        sa.Column("salary_currency", sa.String(8)),
        sa.Column("salary_period", sa.String(16)),
        sa.Column(
            "salary_known", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("salary_ref_min", sa.Numeric(14, 2)),
        sa.Column("salary_ref_max", sa.Numeric(14, 2)),
        _dt("llm_filled_at"),
        sa.Column(
            "fill_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("embedding", Vector(1024)),
        sa.Column("model_version", sa.String(128)),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint(
            "user_id", "company_id", "external_id",
            name="uq_jobs_user_company_external",
        ),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_user_status", "jobs", ["user_id", "status"])
    op.create_index(
        "ix_jobs_user_company_fingerprint", "jobs",
        ["user_id", "company_id", "fingerprint"],
    )
    op.create_index(
        "ix_jobs_user_model_version", "jobs", ["user_id", "model_version"]
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("companies")
