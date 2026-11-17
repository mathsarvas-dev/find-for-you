"""Applications (candidatura): create, list, detail, generate, template, line edits.

Every query is scoped by the caller's user_id; another user's row is a 404.
The LLM is never called here: generation only flips the row to 'generating'
and the worker does the rest. Nothing leaves the computer: no form is filled
or sent.
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.matching import INT4_MAX, model_version
from rumo_core.applications.coverage import compute_coverage
from rumo_core.applications.lines import DocLine, document_status, locale_for, template_for
from rumo_core.applications.pdf import PdfUnavailable
from rumo_core.applications.render import (
    render_html,
    render_letter_html,
    render_letter_text,
    render_pdf,
    render_text,
)
from rumo_core.applications.profile_index import item_texts
from rumo_core.applications.quota import generated_last_24h
from rumo_core.applications.requirements import Requirements
from rumo_core.applications.validator import build_context, validate_line
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.matching.ai_score import JobView
from rumo_core.models import Application, ApplicationDocument, Company, Job, Profile
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile
from rumo_core.matching.ranking import usable_profile

router = APIRouter(tags=["applications"])

MSG_JOB_NOT_FOUND = "Vaga não encontrada."
MSG_APP_NOT_FOUND = "Candidatura não encontrada."
MSG_GENERATING = "A candidatura já está sendo gerada."
MSG_APPLIED_LOCKED = 'Desfaça o "Apliquei" antes de gerar de novo.'
MSG_NO_PROFILE = "Confirme seu perfil antes de preparar candidaturas."
MSG_NOT_VECTORIZED = "Seu perfil ainda está sendo processado. Tente novamente em instantes."
MSG_DAILY_LIMIT = "Limite diário de {n} candidaturas geradas atingido. Tente de novo amanhã."
MSG_FACT_LINE = "Este dado vem do seu perfil. Para mudar, edite o perfil."
MSG_LINE_NOT_FOUND = "Linha não encontrada."
MSG_NO_DOCUMENT = "Esta candidatura ainda não tem currículo."
MSG_REFEREES_AU_ONLY = "Referências só se aplicam ao modelo da Austrália."
MSG_PDF_BLOCKED = "Corrija as linhas marcadas antes de baixar."
MSG_PDF_UNAVAILABLE = "Não foi possível gerar o PDF agora. Use a versão em texto."
MSG_NO_RESUME = "O currículo ainda não foi gerado."
MSG_NO_LETTER = "A carta ainda não foi gerada."

WARN_PROFILE_CHANGED = "profile_changed"
WARN_JOB_CHANGED = "job_changed"
WARN_JOB_CLOSED = "job_closed"


# ---- schemas --------------------------------------------------------------

class CreateIn(BaseModel):
    job_id: int = Field(ge=1, le=INT4_MAX)


class PatchIn(BaseModel):
    template: Literal["us", "de", "au", "br"] | None = None
    referees: str | None = Field(default=None, max_length=1000)


class LineIn(BaseModel):
    text: str = Field(max_length=600)


class ApplicationOut(BaseModel):
    id: int
    job_id: int
    state: str
    error: str | None
    locale: str
    template: str
    referees: str | None
    profile_version: int | None
    applied_at: datetime | None


class ListItem(BaseModel):
    id: int
    job_id: int
    job_title: str
    company_name: str
    state: str
    updated_at: datetime
    applied_at: datetime | None
    coverage: dict | None


class ListOut(BaseModel):
    items: list[ListItem]


class DocumentOut(BaseModel):
    version: int
    status: str
    lines: list[DocLine]


class CanGenerate(BaseModel):
    allowed: bool
    reason: str | None


class JobOut(BaseModel):
    id: int
    title: str
    company_name: str
    url: str
    status: str


class ApplicationDetail(BaseModel):
    id: int
    state: str
    error: str | None
    locale: str
    template: str
    referees: str | None
    profile_version: int | None
    current_profile_version: int | None
    warnings: list[str]
    job: JobOut
    requirements: dict | None
    coverage: dict | None
    resume: DocumentOut | None
    cover_letter: DocumentOut | None
    questions: list[dict] = []
    tasks: list[dict]
    can_generate: CanGenerate
    applied_at: datetime | None


# ---- helpers --------------------------------------------------------------

def _out(a: Application) -> ApplicationOut:
    return ApplicationOut(
        id=a.id, job_id=a.job_id, state=a.state, error=a.error, locale=a.locale,
        template=a.template, referees=a.referees, profile_version=a.profile_version,
        applied_at=a.applied_at,
    )


def _job_row(session: Session, user_id: int, job_id: int):
    return session.execute(
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.id == job_id, Job.user_id == user_id, Company.user_id == user_id)
    ).first()


def _app_or_404(session: Session, user_id: int, app_id: int) -> Application:
    app = session.scalar(
        select(Application).where(Application.id == app_id, Application.user_id == user_id)
    )
    if app is None:
        raise HTTPException(404, MSG_APP_NOT_FOUND)
    return app


def _latest_doc(session: Session, app_id: int, kind: str) -> ApplicationDocument | None:
    return session.scalar(
        select(ApplicationDocument)
        .where(ApplicationDocument.application_id == app_id, ApplicationDocument.kind == kind)
        .order_by(ApplicationDocument.version.desc())
        .limit(1)
    )


def _doc_out(doc: ApplicationDocument | None) -> DocumentOut | None:
    if doc is None:
        return None
    return DocumentOut(
        version=doc.version, status=doc.status,
        lines=[DocLine.model_validate(x) for x in doc.lines],
    )


def _generate_block(session: Session, app: Application, user_id: int, mv: str) -> tuple[int, str] | None:
    """(status, message) when the application cannot be generated now, else None."""
    if app.state == "generating":
        return 409, MSG_GENERATING
    if app.state == "applied":
        return 409, MSG_APPLIED_LOCKED
    if get_current_profile(session, user_id) is None:
        return 409, MSG_NO_PROFILE
    version, used = usable_profile(session, user_id, mv)
    if version is None or used == 0:
        return 409, MSG_NOT_VECTORIZED
    limit = get_settings().application_daily_limit
    if generated_last_24h(session) >= limit:
        return 429, MSG_DAILY_LIMIT.format(n=limit)
    return None


def _context(session: Session, app: Application, user_id: int):
    """Validation context: the profile version the application was written against."""
    prof = session.scalar(select(Profile).where(
        Profile.user_id == user_id, Profile.version == app.profile_version))
    profile = CanonicalProfile.model_validate(prof.data) if prof is not None else CanonicalProfile()
    row = _job_row(session, user_id, app.job_id)
    job = None
    if row is not None:
        j, company_name = row
        job = JobView(
            title=j.title, company_name=company_name, location_text=j.location_text,
            work_model=j.work_model, seniority=j.seniority, department=j.department,
            description=j.description,
        )
    return profile, job, build_context(profile, job, app.locale)


# ---- routes ---------------------------------------------------------------

@router.post("/applications", response_model=ApplicationOut)
def create_application(
    body: CreateIn,
    response: Response,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    row = _job_row(session, user_id, body.job_id)
    if row is None:
        raise HTTPException(404, MSG_JOB_NOT_FOUND)
    job, _ = row
    existing = session.scalar(select(Application).where(
        Application.user_id == user_id, Application.job_id == body.job_id))
    if existing is not None:
        return _out(existing)
    new_id = session.execute(
        insert(Application)
        .values(
            user_id=user_id, job_id=body.job_id, state="draft", attempts=0, tasks=[],
            locale=locale_for(job.language), template=template_for(job.country),
        )
        .on_conflict_do_nothing(constraint="uq_applications_user_job")
        .returning(Application.id)
    ).scalar()
    session.commit()
    if new_id is None:  # a concurrent request created it first
        existing = session.scalar(select(Application).where(
            Application.user_id == user_id, Application.job_id == body.job_id))
        return _out(existing)
    response.status_code = 201
    return _out(session.get(Application, new_id))


@router.get("/applications", response_model=ListOut)
def list_applications(
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    rows = session.execute(
        select(Application, Job.title, Company.name)
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
        .where(Application.user_id == user_id)
        .order_by(Application.updated_at.desc(), Application.id.desc())
    ).all()
    items = []
    for app, title, company in rows:
        cov = app.coverage
        items.append(ListItem(
            id=app.id, job_id=app.job_id, job_title=title, company_name=company,
            state=app.state, updated_at=app.updated_at, applied_at=app.applied_at,
            coverage={"must": cov["must"], "nice": cov["nice"]} if cov else None,
        ))
    return ListOut(items=items)


@router.get("/applications/{app_id}", response_model=ApplicationDetail)
def application_detail(
    app_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    app = _app_or_404(session, user_id, app_id)
    row = _job_row(session, user_id, app.job_id)
    if row is None:
        raise HTTPException(404, MSG_JOB_NOT_FOUND)
    job, company_name = row
    current = get_current_profile(session, user_id)
    current_version = current.version if current is not None else None
    warnings: list[str] = []
    if app.profile_version is not None and current_version is not None \
            and current_version != app.profile_version:
        warnings.append(WARN_PROFILE_CHANGED)
    if app.job_content_hash and job.content_hash != app.job_content_hash:
        warnings.append(WARN_JOB_CHANGED)
    if job.status != "open":
        warnings.append(WARN_JOB_CLOSED)
    block = _generate_block(session, app, user_id, mv)
    return ApplicationDetail(
        id=app.id, state=app.state, error=app.error, locale=app.locale,
        template=app.template, referees=app.referees, profile_version=app.profile_version,
        current_profile_version=current_version, warnings=warnings,
        job=JobOut(id=job.id, title=job.title, company_name=company_name,
                   url=job.url, status=job.status),
        requirements=app.requirements, coverage=app.coverage,
        resume=_doc_out(_latest_doc(session, app.id, "resume")),
        cover_letter=_doc_out(_latest_doc(session, app.id, "cover_letter")),
        tasks=list(app.tasks or []),
        can_generate=CanGenerate(allowed=block is None, reason=block[1] if block else None),
        applied_at=app.applied_at,
    )


@router.post("/applications/{app_id}/generate", response_model=ApplicationOut, status_code=202)
def generate_application(
    app_id: int,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
    mv: str = Depends(model_version),
):
    app = _app_or_404(session, user_id, app_id)
    block = _generate_block(session, app, user_id, mv)
    if block is not None:
        status, message = block
        headers = {"Retry-After": "3600"} if status == 429 else None
        raise HTTPException(status, message, headers=headers)
    # atomic: only a row that is not already generating (or applied) is taken
    result = session.execute(
        update(Application)
        .where(
            Application.id == app_id,
            Application.user_id == user_id,
            Application.state.notin_(["generating", "applied"]),
        )
        .values(state="generating", attempts=0, error=None)
    )
    if result.rowcount == 0:
        session.rollback()
        raise HTTPException(409, MSG_GENERATING)
    session.commit()
    session.refresh(app)
    return _out(app)


@router.patch("/applications/{app_id}", response_model=ApplicationOut)
def patch_application(
    app_id: int,
    body: PatchIn,
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    app = _app_or_404(session, user_id, app_id)
    new_template = body.template or app.template
    if body.referees and new_template != "au":
        raise HTTPException(422, MSG_REFEREES_AU_ONLY)
    if body.template is not None:
        app.template = body.template
    if body.referees is not None:
        app.referees = body.referees.strip() or None
    session.commit()
    return _out(app)


@router.put("/applications/{app_id}/lines/{line_id}", response_model=DocumentOut)
def edit_line(
    app_id: int,
    line_id: str,
    body: LineIn,
    kind: Literal["resume", "cover_letter"] = "resume",
    session: Session = Depends(get_session),
    user_id: int = Depends(current_user_id),
):
    app = _app_or_404(session, user_id, app_id)
    doc = _latest_doc(session, app.id, kind)
    if doc is None:
        raise HTTPException(404, MSG_NO_DOCUMENT)
    lines = [DocLine.model_validate(x) for x in doc.lines]
    line = next((x for x in lines if x.id == line_id), None)
    if line is None:
        raise HTTPException(404, MSG_LINE_NOT_FOUND)
    if line.origin == "fact":
        raise HTTPException(409, MSG_FACT_LINE)

    line.text = body.text.strip()
    line.origin = "user"
    profile, _, ctx = _context(session, app, user_id)
    line.violations = validate_line(line, ctx)

    doc.lines = [x.model_dump(mode="json") for x in lines]
    doc.status = document_status(lines)
    if kind == "resume" and app.requirements is not None:
        reqs = Requirements.model_validate(app.requirements)
        decisions = {
            item["id"]: item["decision"]
            for item in (app.coverage or {}).get("items", [])
            if item.get("decision")
        }
        app.coverage = compute_coverage(reqs, lines, item_texts(profile), decisions)
    session.commit()
    return _doc_out(doc)


# ---- downloads ------------------------------------------------------------

def _send_document(session: Session, user_id: int, app_id: int, *, kind: str, fmt: str) -> Response:
    """PDF or plain text of the current document. Only a document whose lines
    all pass the validator is served; the attachment name is built from the
    application id, never from the posting."""
    app = _app_or_404(session, user_id, app_id)
    doc = _latest_doc(session, app.id, kind)
    if doc is None:
        raise HTTPException(404, MSG_NO_RESUME if kind == "resume" else MSG_NO_LETTER)
    if doc.status != "valid":
        raise HTTPException(409, MSG_PDF_BLOCKED)
    lines = [DocLine.model_validate(x) for x in doc.lines]
    today = date.today()

    if kind == "resume":
        referees = app.referees if app.template == "au" else None
        html = render_html(lines, template=app.template, locale=app.locale, referees=referees)
        text = render_text(lines, locale=app.locale, referees=referees, template=app.template)
        stem = "curriculo"
    else:
        row = _job_row(session, user_id, app.job_id)
        job_title, company_name = (row[0].title, row[1]) if row is not None else ("", "")
        resume = _latest_doc(session, app.id, "resume")
        sender = [DocLine.model_validate(x) for x in resume.lines] if resume is not None else []
        sender = [x for x in sender if x.section == "contact"]
        html = render_letter_html(lines, template=app.template, locale=app.locale,
                                  job_title=job_title, company_name=company_name,
                                  sender_lines=sender, today=today)
        text = render_letter_text(lines, locale=app.locale, job_title=job_title,
                                  company_name=company_name, sender_lines=sender, today=today)
        stem = "carta"

    headers = {"Content-Disposition": f'attachment; filename="{stem}-{app.id}.{fmt}"'}
    if fmt == "txt":
        return Response(text, media_type="text/plain; charset=utf-8", headers=headers)
    try:
        pdf = render_pdf(html)
    except PdfUnavailable:
        raise HTTPException(503, MSG_PDF_UNAVAILABLE) from None
    return Response(pdf, media_type="application/pdf", headers=headers)


@router.get("/applications/{app_id}/resume.pdf")
def resume_pdf(app_id: int, session: Session = Depends(get_session),
               user_id: int = Depends(current_user_id)):
    return _send_document(session, user_id, app_id, kind="resume", fmt="pdf")


@router.get("/applications/{app_id}/resume.txt")
def resume_txt(app_id: int, session: Session = Depends(get_session),
               user_id: int = Depends(current_user_id)):
    return _send_document(session, user_id, app_id, kind="resume", fmt="txt")


@router.get("/applications/{app_id}/cover-letter.pdf")
def cover_letter_pdf(app_id: int, session: Session = Depends(get_session),
                     user_id: int = Depends(current_user_id)):
    return _send_document(session, user_id, app_id, kind="cover_letter", fmt="pdf")


@router.get("/applications/{app_id}/cover-letter.txt")
def cover_letter_txt(app_id: int, session: Session = Depends(get_session),
                     user_id: int = Depends(current_user_id)):
    return _send_document(session, user_id, app_id, kind="cover_letter", fmt="txt")
