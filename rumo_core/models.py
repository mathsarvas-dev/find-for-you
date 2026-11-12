from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rumo_core.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserScopedMixin:
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)


class ProfileImport(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "profile_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    # pending | extracting | ready | error
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    extracted: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class Profile(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "version", name="uq_profiles_user_version"),
        Index("ix_profiles_user_content_hash", "user_id", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ProfileChunk(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "profile_chunks"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "profile_version", "source_ref",
            name="uq_profile_chunks_user_version_source",
        ),
        Index("ix_profile_chunks_user_model_version", "user_id", "model_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_version: Mapped[int] = mapped_column(nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    model_version: Mapped[str | None] = mapped_column(String(128))


class InterviewSession(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        Index("ix_interview_sessions_user_state", "user_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_version: Mapped[int] = mapped_column(nullable=False)
    # generating | working | awaiting_answer | done | error
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="generating"
    )
    asked_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    max_questions: Mapped[int] = mapped_column(nullable=False, server_default="12")
    handled_gaps: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    current_gap_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)


class ChatMessage(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session_id_id", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # interview | filters
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="interview"
    )
    # assistant | user
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # question | answer | skip | info
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    gap_id: Mapped[str | None] = mapped_column(String(255))


class ProfileSuggestion(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "profile_suggestions"
    __table_args__ = (
        Index("ix_profile_suggestions_session_status", "session_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    gap_id: Mapped[str] = mapped_column(String(255), nullable=False)
    op: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # pending | accepted | adjusted | rejected
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    applied_version: Mapped[int | None] = mapped_column()


class AnswerBankItem(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "answer_bank"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_answer_bank_user_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[int | None] = mapped_column()
