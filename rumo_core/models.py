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
