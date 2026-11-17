"""candidatura: applications, documents, questions; answer_bank.lang

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
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


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "job_id", sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("profile_version", sa.Integer()),
        sa.Column("job_content_hash", sa.String(64)),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("template", sa.String(8), nullable=False),
        sa.Column("requirements", JSONB()),
        sa.Column("coverage", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column(
            "tasks", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("referees", sa.Text()),
        *_ts(),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
        sa.CheckConstraint(
            "state in ('draft','generating','ready','error','applied')",
            name="ck_applications_state",
        ),
        sa.CheckConstraint(
            "template in ('us','de','au','br')", name="ck_applications_template",
        ),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_user_state", "applications", ["user_id", "state"])

    op.create_table(
        "application_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "application_id", sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("lines", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_ts(),
        sa.UniqueConstraint(
            "application_id", "kind", "version",
            name="uq_application_documents_app_kind_version",
        ),
        sa.CheckConstraint(
            "kind in ('resume','cover_letter')", name="ck_application_documents_kind",
        ),
        sa.CheckConstraint(
            "status in ('valid','needs_fix')", name="ck_application_documents_status",
        ),
    )
    op.create_index("ix_application_documents_user_id", "application_documents", ["user_id"])
    op.create_index(
        "ix_application_documents_created", "application_documents", ["kind", "created_at"],
    )

    op.create_table(
        "application_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "application_id", sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("options", JSONB()),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("answer", sa.Text()),
        sa.Column(
            "answer_state", sa.String(8), nullable=False, server_default="empty",
        ),
        sa.Column("bank_key", sa.String(64)),
        *_ts(),
        sa.CheckConstraint(
            "source in ('api','pasted')", name="ck_application_questions_source",
        ),
        sa.CheckConstraint(
            "kind in ('text','choice','bool','file')", name="ck_application_questions_kind",
        ),
        sa.CheckConstraint(
            "answer_state in ('empty','draft','edited','final')",
            name="ck_application_questions_answer_state",
        ),
    )
    op.create_index(
        "ix_application_questions_user_id", "application_questions", ["user_id"],
    )

    op.add_column(
        "answer_bank",
        sa.Column("lang", sa.String(8), nullable=False, server_default="pt"),
    )


def downgrade() -> None:
    op.drop_column("answer_bank", "lang")
    op.drop_table("application_questions")
    op.drop_table("application_documents")
    op.drop_table("applications")
