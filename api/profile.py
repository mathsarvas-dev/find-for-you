"""Profile routes: resume import, review and confirmation (scoped by user)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.models import ProfileChunk, ProfileImport
from rumo_core.profile.provisional import (
    ProfileAlreadyStructured,
    create_provisional_profile,
)
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import confirm_profile, get_current_profile
from rumo_core.profile.text_extract import (
    TextExtractError,
    UnsupportedTypeError,
    extract_text,
)

router = APIRouter(prefix="/profile", tags=["profile"])


class ImportCreated(BaseModel):
    id: int
    status: str


class ImportOut(BaseModel):
    id: int
    status: str
    error: str | None
    truncated: bool
    profile: CanonicalProfile | None
    retry_at: datetime | None = None


class ConfirmIn(BaseModel):
    profile: CanonicalProfile
    narrative: str | None = None


class ProfileStatus(BaseModel):
    version: int
    chunks_total: int
    chunks_embedded: int


class PendingImport(BaseModel):
    id: int
    status: str
    retry_at: datetime | None


class ProfileOut(ProfileStatus):
    profile: CanonicalProfile
    narrative: str | None
    provisional: bool = False
    # latest resume import, only reported while the profile is provisional
    import_: PendingImport | None = Field(default=None, serialization_alias="import")


def _status(session: Session, user_id: int, version: int) -> ProfileStatus:
    base = (ProfileChunk.user_id == user_id, ProfileChunk.profile_version == version)
    total = session.scalar(select(func.count()).select_from(ProfileChunk).where(*base))
    embedded = session.scalar(
        select(func.count()).select_from(ProfileChunk)
        .where(*base, ProfileChunk.embedding.is_not(None))
    )
    return ProfileStatus(version=version, chunks_total=total, chunks_embedded=embedded)


@router.post("/imports", status_code=201, response_model=ImportCreated)
def create_import(
    file: UploadFile,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    limit = get_settings().upload_max_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "Arquivo muito grande.")
    filename = file.filename or "arquivo"
    content_type = file.content_type or "application/octet-stream"
    try:
        text = extract_text(data, content_type, filename)
    except UnsupportedTypeError as exc:
        raise HTTPException(415, str(exc))
    except TextExtractError as exc:
        raise HTTPException(422, str(exc))
    if not text.strip():
        raise HTTPException(422, "Não consegui ler texto do arquivo.")
    row = ProfileImport(
        user_id=user_id, filename=filename[:512], content_type=content_type[:255],
        raw_text=text, status="pending",
    )
    session.add(row)
    session.commit()
    return ImportCreated(id=row.id, status=row.status)


@router.get("/imports/{import_id}", response_model=ImportOut)
def get_import(
    import_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = session.scalars(
        select(ProfileImport).where(
            ProfileImport.id == import_id, ProfileImport.user_id == user_id
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Importação não encontrada.")
    profile = (
        CanonicalProfile.model_validate(row.extracted)
        if row.status == "ready" and row.extracted is not None
        else None
    )
    return ImportOut(
        id=row.id, status=row.status, error=row.error,
        truncated=row.truncated, profile=profile, retry_at=row.retry_at,
    )


@router.post(
    "/imports/{import_id}/provisional", status_code=201, response_model=ProfileStatus
)
def continue_without_extraction(
    import_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    """Provisional profile from the raw resume text (no LLM); the worker embeds it."""
    row = session.scalars(
        select(ProfileImport).where(
            ProfileImport.id == import_id, ProfileImport.user_id == user_id
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Importação não encontrada.")
    try:
        prof = create_provisional_profile(session, user_id, row.raw_text or "")
    except ProfileAlreadyStructured:
        raise HTTPException(409, "Você já tem um perfil confirmado.")
    except ValueError:
        raise HTTPException(422, "Não consegui ler texto do arquivo.")
    return _status(session, user_id, prof.version)


@router.post("/confirm", response_model=ProfileStatus)
def confirm(
    body: ConfirmIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = confirm_profile(session, user_id, body.profile, body.narrative)
    return _status(session, user_id, row.version)


@router.get("", response_model=ProfileOut)
def get_profile(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = get_current_profile(session, user_id)
    if row is None:
        raise HTTPException(404, "Perfil não encontrado.")
    st = _status(session, user_id, row.version)
    imp = None
    if row.provisional:
        last = session.scalars(
            select(ProfileImport).where(ProfileImport.user_id == user_id)
            .order_by(ProfileImport.id.desc()).limit(1)
        ).first()
        if last is not None:
            imp = PendingImport(id=last.id, status=last.status, retry_at=last.retry_at)
    return ProfileOut(
        **st.model_dump(),
        profile=CanonicalProfile.model_validate(row.data),
        narrative=row.narrative, provisional=row.provisional, import_=imp,
    )
