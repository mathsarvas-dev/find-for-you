"""interview: sessions, chat messages, profile suggestions, answer bank

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
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
        "interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="generating"
        ),
        sa.Column("asked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_questions", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("handled_gaps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("current_gap_id", sa.String(255)),
        sa.Column("error", sa.Text()),
        _user_id(),
        *_ts(),
    )
    op.create_index(
        "ix_interview_sessions_user_id", "interview_sessions", ["user_id"]
    )
    op.create_index(
        "ix_interview_sessions_user_state", "interview_sessions",
        ["user_id", "state"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id", sa.Integer(),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel", sa.String(32), nullable=False, server_default="interview"
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("gap_id", sa.String(255)),
        _user_id(),
        *_ts(),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index(
        "ix_chat_messages_session_id_id", "chat_messages", ["session_id", "id"]
    )

    op.create_table(
        "profile_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id", sa.Integer(),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id", sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gap_id", sa.String(255), nullable=False),
        sa.Column("op", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("applied_version", sa.Integer()),
        _user_id(),
        *_ts(),
    )
    op.create_index(
        "ix_profile_suggestions_user_id", "profile_suggestions", ["user_id"]
    )
    op.create_index(
        "ix_profile_suggestions_session_status", "profile_suggestions",
        ["session_id", "status"],
    )

    op.create_table(
        "answer_bank",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.Integer()),
        _user_id(),
        *_ts(),
        sa.UniqueConstraint("user_id", "key", name="uq_answer_bank_user_key"),
    )
    op.create_index("ix_answer_bank_user_id", "answer_bank", ["user_id"])


def downgrade() -> None:
    op.drop_table("answer_bank")
    op.drop_table("profile_suggestions")
    op.drop_table("chat_messages")
    op.drop_table("interview_sessions")
