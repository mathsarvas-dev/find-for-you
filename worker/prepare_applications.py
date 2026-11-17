"""Worker job: resume generation for applications in state 'generating' (marco 5).

One application per tick, the same pattern as `worker.match_jobs`: the row is
picked with FOR UPDATE SKIP LOCKED and committed before any network call; the
result is written in a fresh transaction that re-checks the state and the
profile version. If the profile changed while the LLM was answering, the
result is discarded and the application stays 'generating' (no attempt is
counted), so it is regenerated against the new version.

Never logs job text, profile text or LLM output: only ids and exception names.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import func, select

from rumo_core.applications.coverage import compute_coverage
from rumo_core.applications.draft import DraftInvalid, draft_resume
from rumo_core.applications.questions import MAX_BANK_ITEMS, AnswerInvalid, draft_answer
from rumo_core.applications.lines import DocLine, document_status
from rumo_core.applications.profile_index import item_texts
from rumo_core.applications.quota import generated_last_24h
from rumo_core.applications.requirements import Requirements, extract_requirements
from rumo_core.applications.select import select_evidence
from rumo_core.llm.base import LLMProvider, LLMRateLimited
from rumo_core.matching.ai_score import JobView
from rumo_core.models import (
    AnswerBankItem, Application, ApplicationDocument, ApplicationQuestion, Company, Job, Profile,
)
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile
from worker.jobs import EMBED_LOCK
from worker.llm_priority import interactive_llm_busy

logger = logging.getLogger("worker.prepare_applications")

MSG_DRAFT_INVALID = "A IA escreveu algo que não está no seu perfil. Gere de novo ou complete o perfil."
MSG_LLM_FAIL = "Não foi possível gerar agora. Tente de novo mais tarde."
MSG_NO_PROFILE = "Confirme seu perfil antes de preparar candidaturas."

RATE_LIMIT_PAUSE_SECONDS = 60.0
CAP_LOG_EVERY_SECONDS = 3600.0

_warned_no_llm = False
_pause_until = 0.0
_cap_logged_at: float | None = None
# application ids between selection and write in THIS process: overlapping
# ticks in one process never generate the same application twice
_in_flight: set[int] = set()


def _reset_state() -> None:
    global _warned_no_llm, _pause_until, _cap_logged_at
    _warned_no_llm = False
    _pause_until = 0.0
    _cap_logged_at = None
    _in_flight.clear()


class _TimedLLM:
    """Every model call gets its own timeout (the draft may call twice)."""

    def __init__(self, llm: LLMProvider, timeout: float):
        self._llm = llm
        self._timeout = timeout

    async def complete_json(self, messages, schema):
        return await asyncio.wait_for(
            self._llm.complete_json(messages, schema), timeout=self._timeout
        )


@dataclass(frozen=True)
class _Work:
    app_id: int
    user_id: int
    locale: str
    profile: CanonicalProfile
    profile_version: int
    job: JobView
    job_hash: str
    prior_requirements: dict | None
    prior_job_hash: str | None
    prior_coverage: dict | None


def _select_one(session_factory, max_attempts: int, user_id: int | None) -> int | None:
    with session_factory() as s:
        stmt = select(Application.id).where(
            Application.state == "generating",
            Application.attempts < max_attempts,
        )
        if user_id is not None:
            stmt = stmt.where(Application.user_id == user_id)
        if _in_flight:
            stmt = stmt.where(Application.id.notin_(_in_flight))
        app_id = s.execute(
            stmt.order_by(Application.updated_at, Application.id)
            .limit(1)
            .with_for_update(of=Application, skip_locked=True)
        ).scalar_one_or_none()
        s.commit()
    return app_id


def _load(session_factory, app_id: int) -> _Work | str | None:
    """The work item, an error message when the application cannot be generated,
    or None when there is nothing to do (gone, or no longer 'generating')."""
    with session_factory() as s:
        app = s.get(Application, app_id)
        if app is None or app.state != "generating":
            return None
        row = s.execute(
            select(Job, Company.name)
            .join(Company, Company.id == Job.company_id)
            .where(Job.id == app.job_id, Job.user_id == app.user_id)
        ).first()
        if row is None:
            return None
        job, company_name = row
        current = get_current_profile(s, app.user_id)
        if current is None:
            return MSG_NO_PROFILE
        return _Work(
            app_id=app.id,
            user_id=app.user_id,
            locale=app.locale,
            profile=CanonicalProfile.model_validate(current.data),
            profile_version=current.version,
            job=JobView(
                title=job.title, company_name=company_name, location_text=job.location_text,
                work_model=job.work_model, seniority=job.seniority, department=job.department,
                description=job.description,
            ),
            job_hash=job.content_hash,
            prior_requirements=app.requirements,
            prior_job_hash=app.job_content_hash,
            prior_coverage=app.coverage,
        )


def _set_error(session_factory, app_id: int, message: str) -> None:
    with session_factory() as s:
        app = s.execute(
            select(Application).where(Application.id == app_id).with_for_update()
        ).scalar_one_or_none()
        if app is not None and app.state == "generating":
            app.state = "error"
            app.error = message
            s.commit()
        else:
            s.rollback()


def _bump_attempts(session_factory, app_id: int, max_attempts: int) -> None:
    with session_factory() as s:
        app = s.execute(
            select(Application).where(Application.id == app_id).with_for_update()
        ).scalar_one_or_none()
        if app is None or app.state != "generating":
            s.rollback()
            return
        app.attempts += 1
        if app.attempts >= max_attempts:
            app.state = "error"
            app.error = MSG_LLM_FAIL
        s.commit()


def _write_ready(session_factory, work: _Work, reqs: Requirements, lines: list[DocLine]) -> bool:
    """Persist a validated draft in a fresh transaction. False (discarded, the
    application stays 'generating') when it changed meanwhile or the profile
    now has another version."""
    with session_factory() as s:
        app = s.execute(
            select(Application).where(Application.id == work.app_id).with_for_update()
        ).scalar_one_or_none()
        if app is None or app.state != "generating":
            s.rollback()
            return False
        current = get_current_profile(s, app.user_id)
        if current is None or current.version != work.profile_version:
            s.rollback()
            return False
        index = item_texts(CanonicalProfile.model_validate(current.data))
        decisions = {
            item["id"]: item["decision"]
            for item in (app.coverage or {}).get("items", [])
            if item.get("decision")
        }
        coverage = compute_coverage(reqs, lines, index, decisions)
        last = s.scalar(select(func.max(ApplicationDocument.version)).where(
            ApplicationDocument.application_id == app.id,
            ApplicationDocument.kind == "resume",
        )) or 0
        s.add(ApplicationDocument(
            user_id=app.user_id, application_id=app.id, kind="resume",
            lines=[line.model_dump(mode="json") for line in lines],
            status=document_status(lines), version=last + 1,
        ))
        app.requirements = reqs.model_dump(mode="json")
        app.coverage = coverage
        app.profile_version = work.profile_version
        app.job_content_hash = work.job_hash
        app.state = "ready"
        app.attempts = 0
        app.error = None
        s.commit()
        return True


def _embed(embedder, texts: list[str]) -> list[list[float]]:
    with EMBED_LOCK:
        return embedder.embed(texts)


async def _evidence(session_factory, embedder, work: _Work,
                    reqs: Requirements) -> dict[str, list[str]]:
    all_experiences = [e.id for e in work.profile.experiences if e.id]
    req_items = reqs.all_reqs()
    evidence: dict[str, list[str]] = {}
    if req_items:
        try:
            vectors = await asyncio.to_thread(_embed, embedder, [r.text for r in req_items])
            with session_factory() as s:
                evidence = select_evidence(
                    s, work.user_id, work.profile_version, embedder.model_version,
                    {r.id: v for r, v in zip(req_items, vectors, strict=True)}, k=2,
                )
        except Exception as exc:  # noqa: BLE001 - evidence is an optimisation
            logger.warning("evidence selection failed for application %s (%s)",
                           work.app_id, type(exc).__name__)
            evidence = {}
    if not any(evidence.values()):
        evidence = {r.id: all_experiences for r in req_items} or {"all": all_experiences}
    evidence.setdefault("summary", ["summary"])
    return evidence


async def _requirements(timed: _TimedLLM, work: _Work) -> Requirements:
    if work.prior_requirements and work.prior_job_hash == work.job_hash:
        return Requirements.model_validate(work.prior_requirements)
    return await extract_requirements(timed, work.job)


async def prepare_applications(
    session_factory,
    llm: LLMProvider | None,
    embedder,
    *,
    max_attempts: int,
    daily_limit: int | None,
    call_timeout: float = 25.0,
    user_id: int | None = None,
    clock=time.monotonic,
) -> int:
    """Generate the resume of one 'generating' application. Returns 1 when an
    application was processed (ready, error, or failed attempt), else 0.

    No LLM: warns once and does nothing. Paused after a rate limit or timeout
    (for RATE_LIMIT_PAUSE_SECONDS). While an interview waits for the LLM, or
    the daily cap is reached, the tick does nothing (the cap is logged at most
    once per hour). A failing call counts an attempt; at `max_attempts` the
    application becomes 'error'.
    """
    global _warned_no_llm, _pause_until, _cap_logged_at
    if llm is None:
        if not _warned_no_llm:
            logger.warning("GROQ_API_KEY not set: application generation disabled")
            _warned_no_llm = True
        return 0
    if clock() < _pause_until:
        return 0
    if interactive_llm_busy(session_factory):
        return 0
    # the daily cap limits generations only: answer drafts (queued by the person) still run
    capped = False
    if daily_limit is not None:
        with session_factory() as s:
            used = generated_last_24h(s)
        capped = used >= daily_limit
        if capped:
            now = clock()
            if _cap_logged_at is None or now - _cap_logged_at >= CAP_LOG_EVERY_SECONDS:
                logger.info("application generation daily limit (%d per 24 h) reached; skipping",
                            daily_limit)
                _cap_logged_at = now

    if not capped:
        app_id = _select_one(session_factory, max_attempts, user_id)
        if app_id is not None:
            _in_flight.add(app_id)
            try:
                return await _process(session_factory, llm, embedder, app_id,
                                      max_attempts=max_attempts, call_timeout=call_timeout, clock=clock)
            finally:
                _in_flight.discard(app_id)

    picked = _select_task(session_factory, user_id)
    if picked is None:
        return 0
    app_id, qid = picked
    _in_flight.add(app_id)
    try:
        return await _process_answer(session_factory, llm, app_id, qid,
                                     call_timeout=call_timeout, clock=clock)
    finally:
        _in_flight.discard(app_id)


# ---- answer drafts (queued by the person on a question) ---------------------

MSG_ANSWER_INVALID = "Não consegui rascunhar sem inventar. Escreva você."


@dataclass(frozen=True)
class _AnswerWork:
    user_id: int
    locale: str
    profile: CanonicalProfile
    job: JobView
    label: str
    bank: list[tuple[str, str, str]]
    refs: list[str]


def _select_task(session_factory, user_id: int | None) -> tuple[int, int] | None:
    """The oldest application with a queued answer draft, locked with SKIP LOCKED."""
    with session_factory() as s:
        stmt = select(Application.id, Application.tasks).where(
            Application.state.in_(("ready", "applied")),
            func.jsonb_array_length(Application.tasks) > 0,
        )
        if user_id is not None:
            stmt = stmt.where(Application.user_id == user_id)
        if _in_flight:
            stmt = stmt.where(Application.id.notin_(_in_flight))
        rows = s.execute(stmt.order_by(Application.updated_at, Application.id).limit(50)).all()
        target = None
        for app_id, tasks in rows:
            for t in tasks or []:
                if t.get("kind") == "answer" and t.get("state") == "queued":
                    target = (app_id, int(t["qid"]))
                    break
            if target is not None:
                break
        if target is None:
            s.rollback()
            return None
        locked = s.execute(
            select(Application.id).where(Application.id == target[0])
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        s.commit()
    return target if locked is not None else None


def _load_answer(session_factory, app_id: int, qid: int) -> _AnswerWork | None:
    with session_factory() as s:
        app = s.get(Application, app_id)
        q = s.get(ApplicationQuestion, qid)
        if app is None or q is None or q.application_id != app.id:
            return None
        row = s.execute(
            select(Job, Company.name)
            .join(Company, Company.id == Job.company_id)
            .where(Job.id == app.job_id, Job.user_id == app.user_id)
        ).first()
        if row is None:
            return None
        job, company_name = row
        prof = s.execute(select(Profile).where(
            Profile.user_id == app.user_id, Profile.version == app.profile_version)).scalar_one_or_none()
        if prof is None:
            return None
        profile = CanonicalProfile.model_validate(prof.data)
        bank_rows = s.execute(
            select(AnswerBankItem.key, AnswerBankItem.lang, AnswerBankItem.value)
            .where(AnswerBankItem.user_id == app.user_id)
            .order_by(AnswerBankItem.updated_at.desc(), AnswerBankItem.id.desc())
            .limit(MAX_BANK_ITEMS)
        ).all()
        return _AnswerWork(
            user_id=app.user_id,
            locale=app.locale,
            profile=profile,
            job=JobView(
                title=job.title, company_name=company_name, location_text=job.location_text,
                work_model=job.work_model, seniority=job.seniority, department=job.department,
                description=job.description,
            ),
            label=q.label,
            bank=[(k, lang, v) for k, lang, v in bank_rows],
            refs=[e.id for e in profile.experiences if e.id] + ["summary"],
        )


def _update_task(session_factory, app_id: int, qid: int, *, remove: bool = False,
                 state: str | None = None, error: str | None = None) -> None:
    with session_factory() as s:
        app = s.execute(
            select(Application).where(Application.id == app_id).with_for_update()
        ).scalar_one_or_none()
        if app is None:
            s.rollback()
            return
        tasks = []
        for t in app.tasks or []:
            if t.get("kind") == "answer" and t.get("qid") == qid:
                if remove:
                    continue
                t = {**t, "state": state, "error": error}
            tasks.append(t)
        app.tasks = tasks
        s.commit()


def _write_answer(session_factory, app_id: int, qid: int, text: str, bank_key: str | None) -> None:
    """A draft never overwrites what the person wrote or finalised meanwhile."""
    with session_factory() as s:
        q = s.execute(
            select(ApplicationQuestion).where(ApplicationQuestion.id == qid).with_for_update()
        ).scalar_one_or_none()
        if q is not None and q.answer_state in ("empty", "draft"):
            q.answer = text
            q.answer_state = "draft"
            q.bank_key = bank_key
        app = s.execute(
            select(Application).where(Application.id == app_id).with_for_update()
        ).scalar_one_or_none()
        if app is not None:
            app.tasks = [t for t in (app.tasks or [])
                         if not (t.get("kind") == "answer" and t.get("qid") == qid)]
        s.commit()


async def _process_answer(session_factory, llm, app_id: int, qid: int, *,
                          call_timeout: float, clock) -> int:
    global _pause_until
    work = _load_answer(session_factory, app_id, qid)
    if work is None:
        _update_task(session_factory, app_id, qid, remove=True)
        return 1
    timed = _TimedLLM(llm, call_timeout)
    try:
        text, key = await draft_answer(
            timed, work.label, work.job, work.profile, work.refs, work.bank, locale=work.locale,
        )
    except (LLMRateLimited, TimeoutError) as exc:
        _pause_until = clock() + RATE_LIMIT_PAUSE_SECONDS
        logger.warning("LLM %s; pausing answer drafts for %ds",
                       "call timed out" if isinstance(exc, TimeoutError) else "rate limited",
                       int(RATE_LIMIT_PAUSE_SECONDS))
        return 0
    except AnswerInvalid:
        _update_task(session_factory, app_id, qid, state="failed", error=MSG_ANSWER_INVALID)
        return 1
    except Exception as exc:  # noqa: BLE001 - class name only
        logger.warning("answer %s draft failed (%s)", qid, type(exc).__name__)
        _update_task(session_factory, app_id, qid, state="failed", error=MSG_LLM_FAIL)
        return 1
    _write_answer(session_factory, app_id, qid, text, key)
    return 1


async def _process(session_factory, llm, embedder, app_id, *, max_attempts, call_timeout, clock) -> int:
    global _pause_until
    work = _load(session_factory, app_id)
    if work is None:
        return 0
    if isinstance(work, str):
        _set_error(session_factory, app_id, work)
        return 1

    timed = _TimedLLM(llm, call_timeout)
    try:
        reqs = await _requirements(timed, work)
        evidence = await _evidence(session_factory, embedder, work, reqs)
        lines = await draft_resume(timed, work.profile, work.job, reqs, evidence, locale=work.locale)
    except (LLMRateLimited, TimeoutError) as exc:
        _pause_until = clock() + RATE_LIMIT_PAUSE_SECONDS
        logger.warning(
            "LLM %s; pausing application generation for %ds",
            "call timed out" if isinstance(exc, TimeoutError) else "rate limited",
            int(RATE_LIMIT_PAUSE_SECONDS),
        )
        return 0
    except DraftInvalid:
        logger.warning("application %s draft rejected by the validator", app_id)
        _set_error(session_factory, app_id, MSG_DRAFT_INVALID)
        return 1
    except Exception as exc:  # noqa: BLE001 - class name only: never the text
        logger.warning("application %s generation failed (%s)", app_id, type(exc).__name__)
        _bump_attempts(session_factory, app_id, max_attempts)
        return 1

    if not _write_ready(session_factory, work, reqs, lines):
        logger.info("application %s: profile or state changed during generation; discarded", app_id)
        return 0
    return 1
