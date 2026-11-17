"""What Configurações > Serviços shows about the LLM: per configured model, whether it
exists, the quota left (last reading of the rate-limit headers), the expected next try,
plus the queue of work waiting for quota. Reads the database only (never calls Groq)."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rumo_core.config import Settings
from rumo_core.llm.quota import QuotaSnapshot, quota_exhausted
from rumo_core.models import LlmModelStatus, ProfileImport


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _future(value: datetime | None, now: datetime) -> datetime | None:
    return value if value is not None and value > now else None


def _roles(settings: Settings) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for role, model in (
        ("main", settings.groq_model),
        ("fill", settings.groq_fill_model or settings.groq_model),
        ("fallback", settings.groq_fallback_model),
    ):
        if model:
            roles.setdefault(model, []).append(role)
    return roles


def _quota(row: LlmModelStatus) -> dict | None:
    if all(
        v is None
        for v in (row.limit_requests, row.remaining_requests, row.limit_tokens, row.remaining_tokens)
    ):
        return None
    return {
        "requests": {
            "limit": row.limit_requests, "remaining": row.remaining_requests,
            "reset_at": _iso(row.reset_requests_at),
        },
        "tokens": {
            "limit": row.limit_tokens, "remaining": row.remaining_tokens,
            "reset_at": _iso(row.reset_tokens_at),
        },
    }


def llm_status(session: Session, settings: Settings, user_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    roles = _roles(settings)
    rows = {
        r.model: r
        for r in session.scalars(
            select(LlmModelStatus).where(LlmModelStatus.model.in_(list(roles)))
        )
    }
    models = []
    for model, model_roles in roles.items():
        row = rows.get(model)
        if row is None:
            models.append({
                "model": model, "roles": model_roles, "exists": None, "missing_since": None,
                "quota": None, "exhausted": False, "observed_at": None,
                "next_try_at": None, "fallback_used_at": None,
            })
            continue
        snap = QuotaSnapshot(
            row.limit_requests, row.remaining_requests, row.reset_requests_at,
            row.limit_tokens, row.remaining_tokens, row.reset_tokens_at,
        )
        models.append({
            "model": model, "roles": model_roles, "exists": row.exists,
            "missing_since": _iso(row.missing_since), "quota": _quota(row),
            "exhausted": quota_exhausted(snap, now), "observed_at": _iso(row.observed_at),
            "next_try_at": _iso(_future(row.next_try_at, now)),
            "fallback_used_at": _iso(row.fallback_used_at),
        })
    queue = []
    count, soonest = session.execute(
        select(func.count(), func.min(ProfileImport.retry_at)).where(
            ProfileImport.user_id == user_id,
            ProfileImport.status == "pending",
            ProfileImport.retry_at.is_not(None),
        )
    ).one()
    if count:
        queue.append({"kind": "resume_import", "count": count, "next_try_at": _iso(soonest)})
    return {"models": models, "queue": queue}
