"""Hard-filter funnel at intake: a job that fails the user's filters is stored as a `stub`.

`hard_filter` mirrors `apply_filters` (SQL, matching/filters.py) clause by
clause, including SQL three-valued logic for salary, so the Python decision
equals the set of rows the SQL query keeps (tests/sources/test_funnel_parity.py).
A stub keeps only what is needed to re-evaluate it later: no description, no
raw, no vector, never sent to the LLM, never listed.
"""

import logging
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from rumo_core.matching.filters import SearchFilters
from rumo_core.models import Job, SearchProfile
from rumo_core.sources.base import RawJob
from rumo_core.sources.normalize import NormalizedJob

logger = logging.getLogger("rumo_core.sources.funnel")

_UNACCENT_EXTRA = {
    "ß": "ss", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D",
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "þ": "th", "ð": "d",
}
_PERIOD_FACTOR = {"month": Decimal(12), "hour": Decimal(2080)}


def fold_like_unaccent(text: str) -> str:
    """Mirror of `lower(unaccent(x))` in SQL: NFKD without marks, extra letters, lower."""
    out = "".join(_UNACCENT_EXTRA.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", out)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


@dataclass(frozen=True)
class FilterView:
    title: str
    country: str | None
    work_model: str
    seniority: str
    language: str
    department: str | None
    salary_known: bool
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: str | None


def view_of(raw: RawJob, n: NormalizedJob) -> FilterView:
    return FilterView(
        title=raw.title, country=n.country, work_model=n.work_model, seniority=n.seniority,
        language=n.language, department=n.department, salary_known=n.salary_known,
        salary_min=n.salary_min, salary_max=n.salary_max,
        salary_currency=n.salary_currency, salary_period=n.salary_period,
    )


def view_of_job(job: Job) -> FilterView:
    return FilterView(
        title=job.title, country=job.country, work_model=job.work_model,
        seniority=job.seniority, language=job.language, department=job.department,
        salary_known=bool(job.salary_known), salary_min=job.salary_min,
        salary_max=job.salary_max, salary_currency=job.salary_currency,
        salary_period=job.salary_period,
    )


def _and3(*values):
    if any(v is False for v in values):
        return False
    if any(v is None for v in values):
        return None
    return True


def _or3(*values):
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


def _not3(v):
    return None if v is None else (not v)


def _salary_ok(v: FilterView, f: SearchFilters) -> bool:
    known = v.salary_known
    if v.salary_currency is None:
        cur_eq = None  # SQL: currency = NULL is NULL
    else:
        cur_eq = v.salary_currency == f.salary_currency
    comparable = _and3(known, cur_eq, v.salary_min is not None)
    annual = None
    amount = v.salary_max if v.salary_max is not None else v.salary_min
    if amount is not None:
        annual = amount * _PERIOD_FACTOR.get(v.salary_period or "", Decimal(1))
    passes = _and3(comparable, None if annual is None else annual >= Decimal(str(f.salary_min)))
    unknown = _and3(known, _not3(comparable))
    no_salary = not known
    cond = passes
    if f.include_unknown:
        cond = _or3(cond, unknown)
    if f.accept_no_salary:
        cond = _or3(cond, no_salary)
    return cond is True


def hard_filter(view: FilterView, f: SearchFilters) -> tuple[bool, str | None]:
    keep = f.include_unknown

    if f.countries:
        ok = view.country in f.countries or (keep and view.country is None)
        if not ok:
            return False, "country"
    if f.work_models and not (view.work_model in f.work_models or (keep and view.work_model == "unknown")):
        return False, "work_model"
    if f.seniorities and not (view.seniority in f.seniorities or (keep and view.seniority == "unknown")):
        return False, "seniority"
    if f.languages and not (view.language in f.languages or (keep and view.language == "unknown")):
        return False, "language"
    if f.departments:
        names = [d.strip().lower() for d in f.departments]
        dep = view.department
        ok = (dep is not None and dep.lower() in names) or (keep and dep is None)
        if not ok:
            return False, "department"
    if f.exclude_countries and view.country is not None and view.country in f.exclude_countries:
        return False, "exclude_country"
    if f.exclude_work_models and view.work_model != "unknown" and view.work_model in f.exclude_work_models:
        return False, "exclude_work_model"
    if f.exclude_seniorities and view.seniority != "unknown" and view.seniority in f.exclude_seniorities:
        return False, "exclude_seniority"
    if f.exclude_languages and view.language != "unknown" and view.language in f.exclude_languages:
        return False, "exclude_language"
    if f.exclude_departments and view.department is not None:
        names = [d.strip().lower() for d in f.exclude_departments]
        if view.department.lower() in names:
            return False, "exclude_department"
    if f.title_exclude:
        title = fold_like_unaccent(view.title)
        for term in f.title_exclude:
            if fold_like_unaccent(term) in title:
                return False, "title_exclude"
    if f.salary_min is not None and not _salary_ok(view, f):
        return False, "salary"
    return True, None


def decide_intake(
    view: FilterView, filters: SearchFilters | None, *, keywords: tuple[str, ...] = (), text: str = ""
) -> tuple[Literal["full", "stub"], str | None]:
    if filters is None:
        return "full", None
    ok, reason = hard_filter(view, filters)
    if not ok:
        return "stub", reason
    if keywords:
        haystack = fold_like_unaccent(f"{view.title} {text}")
        if not any(fold_like_unaccent(k) in haystack for k in keywords):
            return "stub", "feed_keywords"
    return "full", None


def funnel_filters(session: Session, user_id: int) -> SearchFilters | None:
    """Filters of the user's default search profile; None = funnel off (invalid JSON)."""
    row = session.scalar(
        select(SearchProfile).where(SearchProfile.user_id == user_id, SearchProfile.is_default.is_(True))
    )
    if row is None:
        return SearchFilters()
    try:
        return SearchFilters.model_validate(row.filters or {})
    except ValidationError:
        logger.warning("user %s: saved filters are invalid; funnel disabled", user_id)
        return None
