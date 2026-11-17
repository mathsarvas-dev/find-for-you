"""LLM model status (R2) and recommendation feedback (R7)

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "llm_model_status",
        sa.Column("model", sa.String(128), primary_key=True),
        sa.Column("exists", sa.Boolean()),
        sa.Column("missing_since", _TS),
        sa.Column("limit_requests", sa.Integer()),
        sa.Column("remaining_requests", sa.Integer()),
        sa.Column("reset_requests_at", _TS),
        sa.Column("limit_tokens", sa.Integer()),
        sa.Column("remaining_tokens", sa.Integer()),
        sa.Column("reset_tokens_at", _TS),
        sa.Column("observed_at", _TS),
        sa.Column("next_try_at", _TS),
        sa.Column("fallback_used_at", _TS),
    )
    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(16)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "job_id", name="uq_recommendation_feedback_user_job"),
        sa.CheckConstraint("verdict in ('right','wrong')", name="ck_recommendation_feedback_verdict"),
        sa.CheckConstraint(
            "reason is null or reason in "
            "('localidade','competencias','senioridade','salario','empresa','idioma')",
            name="ck_recommendation_feedback_reason",
        ),
    )
    op.create_table(
        "calibration_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_calibration_dismissals_user_key"),
    )


def downgrade() -> None:
    op.drop_table("calibration_dismissals")
    op.drop_table("recommendation_feedback")
    op.drop_table("llm_model_status")
