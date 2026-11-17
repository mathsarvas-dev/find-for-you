from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric,
    SmallInteger, String, Text, UniqueConstraint, func, text,
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
    # set while waiting for an LLM quota retry (status stays 'pending')
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # built from raw resume text (no LLM): ranking works, applications do not
    provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


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
    # language of `value` (pt|en|de): applications reuse it only with the same meaning
    lang: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="pt"
    )


class Company(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "identifier",
            name="uq_companies_user_source_identifier",
        ),
        CheckConstraint(
            "origin in ('manual','auto','feed','link')", name="ck_companies_origin"
        ),
        CheckConstraint(
            "collect_mode in ('funnel','all')", name="ck_companies_collect_mode"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # greenhouse
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # board token, lowercase
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    open_jobs: Mapped[int] = mapped_column(nullable=False, server_default="0")
    # manual | auto | feed | link
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual"
    )
    # funnel | all (all = skip the hard-filter funnel for this company)
    collect_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="funnel"
    )


class Job(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "company_id", "external_id",
            name="uq_jobs_user_company_external",
        ),
        Index("ix_jobs_user_status", "user_id", "status"),
        Index(
            "ix_jobs_user_company_fingerprint",
            "user_id", "company_id", "fingerprint",
        ),
        Index("ix_jobs_user_model_version", "user_id", "model_version"),
        Index("ix_jobs_user_intake_status", "user_id", "intake", "status"),
        Index("ix_jobs_user_fingerprint", "user_id", "fingerprint"),
        CheckConstraint("intake in ('full','stub','gated')", name="ck_jobs_intake"),
        CheckConstraint(
            "origin in ('ats','site','feed','link')", name="ck_jobs_origin"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(255))
    location_text: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    # onsite | hybrid | remote | unknown
    work_model: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unknown"
    )
    # intern | junior | mid | senior | lead | manager | director | unknown
    seniority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unknown"
    )
    # pt | en | de | unknown
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unknown"
    )
    department: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # open | closed | duplicate
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    # year | month | hour
    salary_period: Mapped[str | None] = mapped_column(String(16))
    salary_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # filled in marco 4
    salary_ref_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_ref_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    llm_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fill_attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    model_version: Mapped[str | None] = mapped_column(String(128))
    # full | stub | gated (funnel, marco 3b)
    intake: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="full"
    )
    stub_reason: Mapped[str | None] = mapped_column(String(32))
    source_rank: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    # ats | site | feed | link
    origin: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="ats"
    )


class SearchProfile(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "search_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_search_profiles_user_name"),
        Index(
            "uq_search_profiles_one_default", "user_id", unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    filters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    query_text: Mapped[str | None] = mapped_column(Text)
    query_embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    query_model_version: Mapped[str | None] = mapped_column(String(128))
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class Match(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_id", "profile_version",
            name="uq_matches_user_job_version",
        ),
        Index(
            "ix_matches_user_version_score",
            "user_id", "profile_version", text("ai_score DESC"),
        ),
        CheckConstraint(
            "state in ('pendente','ok','erro')", name="ck_matches_state"
        ),
        CheckConstraint(
            "ai_score between 0 and 100", name="ck_matches_ai_score"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    profile_version: Mapped[int] = mapped_column(nullable=False)
    vec_score: Mapped[float] = mapped_column(Float, nullable=False)
    ai_score: Mapped[int | None] = mapped_column()
    rationale: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[list | None] = mapped_column(JSONB)
    gaps: Mapped[list | None] = mapped_column(JSONB)
    # pendente | ok | erro
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pendente"
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # hash of the job content at scoring time
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class JobLabel(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "job_labels"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_job_labels_user_job"),
        CheckConstraint("label in ('good','bad')", name="ck_job_labels_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(8), nullable=False)


class DiscoveryCandidate(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "discovery_candidates"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "token",
            name="uq_discovery_candidates_user_source_token",
        ),
        Index(
            "ix_discovery_candidates_user_state_retry",
            "user_id", "state", "retry_after",
        ),
        CheckConstraint(
            "origin_kind in ('catalog','profile','target','feed')",
            name="ck_discovery_candidates_origin_kind",
        ),
        CheckConstraint(
            "state in ('pending','valid','invalid','known','rejected')",
            name="ck_discovery_candidates_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    name_hint: Mapped[str | None] = mapped_column(String(255))
    # catalog | profile | target
    origin_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # pending | valid | invalid | known | rejected
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    discovery_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    target_companies: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    quality_seed: Mapped[int | None] = mapped_column(Integer)
    # vector cut for the funnel (marco 3b); NULL = no cut
    adherence_vec_floor: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobFeed(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "job_feeds"
    __table_args__ = (
        Index(
            "uq_job_feeds_user_source_params",
            "user_id", "source", text("md5((params)::text)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(
        nullable=False, server_default="0"
    )
    jobs_seen: Mapped[int] = mapped_column(nullable=False, server_default="0")


class PortalState(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "portal_state"
    __table_args__ = (
        UniqueConstraint("user_id", "portal", name="uq_portal_state_user_portal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portal: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    blocked_by_site: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Application(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
        Index("ix_applications_user_state", "user_id", "state"),
        Index("ix_applications_user_stage", "user_id", "stage"),
        CheckConstraint(
            "stage in ('saved','preparing','applied','interview','offer',"
            "'rejected','no_response')",
            name="ck_applications_stage",
        ),
        CheckConstraint(
            "state in ('draft','generating','ready','error','applied')",
            name="ck_applications_state",
        ),
        CheckConstraint(
            "template in ('us','de','au','br')", name="ck_applications_template"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    profile_version: Mapped[int | None] = mapped_column()
    job_content_hash: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft"
    )
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    template: Mapped[str] = mapped_column(String(8), nullable=False)
    requirements: Mapped[dict | None] = mapped_column(JSONB)
    coverage: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # queue of letter / answer drafts for the worker: [{kind, state, error, ...}]
    tasks: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # typed by the person; only used by the Australian template
    referees: Mapped[str | None] = mapped_column(Text)
    # pipeline (marco 6): saved | preparing | applied | interview | offer |
    # rejected | no_response. applied_at is the date of the application.
    stage: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="saved"
    )
    follow_up_at: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class ApplicationDocument(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "application_documents"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "kind", "version",
            name="uq_application_documents_app_kind_version",
        ),
        Index("ix_application_documents_created", "kind", "created_at"),
        CheckConstraint(
            "kind in ('resume','cover_letter')", name="ck_application_documents_kind"
        ),
        CheckConstraint(
            "status in ('valid','needs_fix')", name="ck_application_documents_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    lines: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class ApplicationQuestion(UserScopedMixin, TimestampMixin, Base):
    __tablename__ = "application_questions"
    __table_args__ = (
        CheckConstraint(
            "source in ('api','pasted')", name="ck_application_questions_source"
        ),
        CheckConstraint(
            "kind in ('text','choice','bool','file')", name="ck_application_questions_kind"
        ),
        CheckConstraint(
            "answer_state in ('empty','draft','edited','final')",
            name="ck_application_questions_answer_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    options: Mapped[list | None] = mapped_column(JSONB)
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    answer: Mapped[str | None] = mapped_column(Text)
    answer_state: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="empty"
    )
    bank_key: Mapped[str | None] = mapped_column(String(64))
