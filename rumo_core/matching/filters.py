"""Search filters schema and their SQL application over `Job`.

Unknown values (work_model/seniority/language == 'unknown', country/department
NULL) are kept only when `include_unknown` is true. Salary is compared only for
jobs with `salary_known` and the same currency as the filter, annualised
(year x1, month x12, hour x2080), using max if present else min.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Select, and_, case, func, or_

from rumo_core.models import Job
from rumo_core.sources.country import norm_country

WORK_MODELS = {"onsite", "hybrid", "remote"}
SENIORITIES = {"intern", "junior", "mid", "senior", "lead", "manager", "director"}
LANGUAGES = {"pt", "en", "de"}

_Str = Annotated[str, Field(max_length=255)]
_List = Annotated[list[_Str], Field(max_length=20)]


def _subset(values: list[str], allowed: set[str], name: str) -> list[str]:
    bad = [v for v in values if v not in allowed]
    if bad:
        raise ValueError(f"{name} inválido(s): {bad}")
    return values


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countries: _List = []
    work_models: _List = []
    seniorities: _List = []
    languages: _List = []
    departments: _List = []
    salary_min: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, max_length=8)
    include_unknown: bool = True
    accept_no_salary: bool = True

    @field_validator("countries")
    @classmethod
    def _countries(cls, v):
        out = []
        for c in v:
            n = norm_country(c)
            if n is None:
                raise ValueError("País vazio.")
            out.append(n)
        return out

    @field_validator("work_models")
    @classmethod
    def _wm(cls, v):
        return _subset(v, WORK_MODELS, "work_models")

    @field_validator("seniorities")
    @classmethod
    def _sen(cls, v):
        return _subset(v, SENIORITIES, "seniorities")

    @field_validator("languages")
    @classmethod
    def _lang(cls, v):
        return _subset(v, LANGUAGES, "languages")

    @field_validator("salary_currency")
    @classmethod
    def _cur(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        return v or None

    @model_validator(mode="after")
    def _salary_needs_currency(self):
        if self.salary_min is not None and not self.salary_currency:
            raise ValueError("salary_currency é obrigatório com salary_min.")
        return self


def _annual_salary():
    amount = func.coalesce(Job.salary_max, Job.salary_min)
    factor = case(
        (Job.salary_period == "month", 12),
        (Job.salary_period == "hour", 2080),
        else_=1,
    )
    return amount * factor


def apply_filters(stmt: Select, filters: SearchFilters) -> Select:
    keep_unknown = filters.include_unknown

    def enum_filter(column, values):
        cond = column.in_(values)
        if keep_unknown:
            cond = or_(cond, column == "unknown")
        return cond

    if filters.countries:
        cond = Job.country.in_(filters.countries)
        if keep_unknown:
            cond = or_(cond, Job.country.is_(None))
        stmt = stmt.where(cond)
    if filters.work_models:
        stmt = stmt.where(enum_filter(Job.work_model, filters.work_models))
    if filters.seniorities:
        stmt = stmt.where(enum_filter(Job.seniority, filters.seniorities))
    if filters.languages:
        stmt = stmt.where(enum_filter(Job.language, filters.languages))
    if filters.departments:
        names = [d.strip().lower() for d in filters.departments]
        cond = func.lower(Job.department).in_(names)
        if keep_unknown:
            cond = or_(cond, Job.department.is_(None))
        stmt = stmt.where(cond)
    if filters.salary_min is not None:
        comparable = and_(
            Job.salary_known.is_(True),
            Job.salary_currency == filters.salary_currency,
            Job.salary_min.is_not(None),
        )
        passes = and_(comparable, _annual_salary() >= filters.salary_min)
        # salary_known with another currency (or no amount) is "unknown".
        unknown = and_(Job.salary_known.is_(True), ~comparable)
        no_salary = Job.salary_known.is_(False)
        cond = passes
        if keep_unknown:
            cond = or_(cond, unknown)
        if filters.accept_no_salary:
            cond = or_(cond, no_salary)
        stmt = stmt.where(cond)
    return stmt
