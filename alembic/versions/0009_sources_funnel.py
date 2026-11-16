"""sources funnel: feeds, portal state, job intake/origin, adherence floor

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("collect_mode", sa.String(8), nullable=False, server_default="funnel"),
    )
    op.create_check_constraint(
        "ck_companies_collect_mode", "companies", "collect_mode in ('funnel','all')"
    )
    op.drop_constraint("ck_companies_origin", "companies", type_="check")
    op.create_check_constraint(
        "ck_companies_origin", "companies", "origin in ('manual','auto','feed','link')"
    )

    op.add_column("jobs", sa.Column("intake", sa.String(8), nullable=False, server_default="full"))
    op.add_column("jobs", sa.Column("stub_reason", sa.String(32)))
    op.add_column(
        "jobs", sa.Column("source_rank", sa.SmallInteger, nullable=False, server_default="0")
    )
    op.add_column("jobs", sa.Column("origin", sa.String(8), nullable=False, server_default="ats"))
    op.create_check_constraint("ck_jobs_intake", "jobs", "intake in ('full','stub','gated')")
    op.create_check_constraint("ck_jobs_origin", "jobs", "origin in ('ats','site','feed','link')")
    op.create_index("ix_jobs_user_intake_status", "jobs", ["user_id", "intake", "status"])
    op.create_index("ix_jobs_user_fingerprint", "jobs", ["user_id", "fingerprint"])

    op.drop_constraint(
        "ck_discovery_candidates_origin_kind", "discovery_candidates", type_="check"
    )
    op.create_check_constraint(
        "ck_discovery_candidates_origin_kind",
        "discovery_candidates",
        "origin_kind in ('catalog','profile','target','feed')",
    )

    op.add_column("user_preferences", sa.Column("adherence_vec_floor", sa.Float))

    op.create_table(
        "job_feeds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("jobs_seen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_job_feeds_user_source_params "
        "ON job_feeds (user_id, source, md5((params)::text))"
    )

    op.create_table(
        "portal_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("portal", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("blocked_by_site", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("blocked_at", sa.DateTime(timezone=True)),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "portal", name="uq_portal_state_user_portal"),
    )


def downgrade() -> None:
    # feed/link companies (and their jobs, by cascade) and feed candidates go first
    op.execute("DELETE FROM companies WHERE origin IN ('feed','link')")
    op.execute("DELETE FROM discovery_candidates WHERE origin_kind = 'feed'")
    op.drop_table("portal_state")
    op.drop_table("job_feeds")
    op.drop_column("user_preferences", "adherence_vec_floor")
    op.drop_constraint(
        "ck_discovery_candidates_origin_kind", "discovery_candidates", type_="check"
    )
    op.create_check_constraint(
        "ck_discovery_candidates_origin_kind",
        "discovery_candidates",
        "origin_kind in ('catalog','profile','target')",
    )
    op.drop_index("ix_jobs_user_fingerprint", table_name="jobs")
    op.drop_index("ix_jobs_user_intake_status", table_name="jobs")
    op.drop_constraint("ck_jobs_origin", "jobs", type_="check")
    op.drop_constraint("ck_jobs_intake", "jobs", type_="check")
    op.drop_column("jobs", "origin")
    op.drop_column("jobs", "source_rank")
    op.drop_column("jobs", "stub_reason")
    op.drop_column("jobs", "intake")
    op.drop_constraint("ck_companies_origin", "companies", type_="check")
    op.create_check_constraint(
        "ck_companies_origin", "companies", "origin in ('manual','auto')"
    )
    op.drop_constraint("ck_companies_collect_mode", "companies", type_="check")
    op.drop_column("companies", "collect_mode")
