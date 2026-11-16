"""The Python hard filter must keep exactly the rows the SQL filter keeps."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters, apply_filters
from rumo_core.models import Company, Job, User
from rumo_core.sources.funnel import hard_filter, view_of_job

pytestmark = pytest.mark.db


def _job(i, **kw):
    base = dict(
        external_id=f"p{i}", title="Engenheiro de Dados", url="https://example.test/j",
        description="d", raw={}, content_hash=f"h{i}", fingerprint=f"f{i}", country="BR",
        work_model="remote", seniority="senior", language="pt", department="Engenharia",
        salary_known=False, salary_min=None, salary_max=None, salary_currency=None,
        salary_period=None,
    )
    base.update(kw)
    return base


JOBS = [
    _job(1),
    _job(2, country=None, work_model="unknown", seniority="unknown", language="unknown"),
    _job(3, country="DE", work_model="onsite", seniority="junior", language="de"),
    _job(4, department=None),
    _job(5, department="  engenharia "),
    _job(6, department="ENGENHARIA"),
    _job(7, title="Engenheiro (PLANTÃO)"),
    _job(8, title="Straße Planner"),
    _job(9, title="Øresund Lead"),
    _job(10, title="Bonus 100% remoto"),
    _job(11, title="a_b dev"),
    _job(12, title="axb dev"),
    _job(13, salary_known=True, salary_min=Decimal("100000"), salary_max=Decimal("120000"),
         salary_currency="USD", salary_period="year"),
    _job(14, salary_known=True, salary_min=Decimal("9000"), salary_max=None,
         salary_currency="USD", salary_period="month"),
    _job(15, salary_known=True, salary_min=Decimal("50"), salary_max=Decimal("60"),
         salary_currency="USD", salary_period="hour"),
    _job(16, salary_known=True, salary_min=Decimal("100000"), salary_max=None,
         salary_currency="EUR", salary_period="year"),
    _job(17, salary_known=True, salary_min=None, salary_max=None, salary_currency=None,
         salary_period=None),
    _job(18, salary_known=True, salary_min=Decimal("99999.99"), salary_max=None,
         salary_currency="USD", salary_period="year"),
    _job(19, salary_known=True, salary_min=Decimal("100000"), salary_max=None,
         salary_currency=None, salary_period="year"),
    _job(20, salary_known=False),
    _job(21, country="PT", work_model="hybrid", seniority="mid", language="en",
         department="Vendas", salary_known=True, salary_min=Decimal("200000"),
         salary_max=Decimal("210000"), salary_currency="BRL", salary_period="year"),
    _job(22, country="BR", work_model="unknown", language="pt", seniority="lead"),
    _job(23, country="BR", work_model="onsite", seniority="manager", language="en"),
    _job(24, country="BR", work_model="remote", seniority="director", language="de",
         department="Dados"),
    _job(25, title="Engenheiro (plantao) de Dados"),
    _job(26, title="Ângela Lead", country="BR"),
    _job(27, title="Engenheiro sênior 100% remoto", salary_known=True,
         salary_min=Decimal("120000"), salary_currency="USD", salary_period="year"),
    _job(28, country="BR", work_model="unknown", seniority="unknown", language="unknown",
         department=None, salary_known=True, salary_min=Decimal("1"), salary_currency="BRL",
         salary_period="month"),
    _job(29, salary_known=True, salary_min=Decimal("100000"), salary_max=Decimal("100000"),
         salary_currency="USD", salary_period="year"),
    _job(30, country="BR", title="Dados sem sal", salary_known=True, salary_min=None,
         salary_max=Decimal("5"), salary_currency="USD", salary_period="year"),
]

CASES = [
    SearchFilters(),
    SearchFilters(countries=["BR"]),
    SearchFilters(countries=["BR"], include_unknown=False),
    SearchFilters(work_models=["remote"]),
    SearchFilters(work_models=["remote"], include_unknown=False),
    SearchFilters(seniorities=["senior", "lead"]),
    SearchFilters(seniorities=["senior"], include_unknown=False),
    SearchFilters(languages=["pt"], include_unknown=False),
    SearchFilters(languages=["en", "de"]),
    SearchFilters(departments=["engenharia"]),
    SearchFilters(departments=["engenharia"], include_unknown=False),
    SearchFilters(departments=["  Dados "], include_unknown=False),
    SearchFilters(exclude_countries=["DE"]),
    SearchFilters(exclude_work_models=["onsite"]),
    SearchFilters(exclude_seniorities=["director", "manager"]),
    SearchFilters(exclude_languages=["de"]),
    SearchFilters(exclude_departments=["vendas"]),
    SearchFilters(title_exclude=["plantao"]),
    SearchFilters(title_exclude=["strasse"]),
    SearchFilters(title_exclude=["oresund"]),
    SearchFilters(title_exclude=["100%"]),
    SearchFilters(title_exclude=["a_b"]),
    SearchFilters(title_exclude=["angela"]),
    SearchFilters(salary_min=100000, salary_currency="USD", accept_no_salary=False),
    SearchFilters(salary_min=100000, salary_currency="USD", accept_no_salary=True),
    SearchFilters(salary_min=100000, salary_currency="USD", include_unknown=False,
                  accept_no_salary=False),
    SearchFilters(salary_min=100000, salary_currency="USD", include_unknown=False),
    SearchFilters(salary_min=1, salary_currency="BRL", include_unknown=True),
    SearchFilters(salary_min=120000, salary_currency="USD", accept_no_salary=False),
    SearchFilters(salary_min=119999.5, salary_currency="USD", accept_no_salary=False),
    SearchFilters(salary_min=100000, salary_currency="USD", accept_no_salary=False,
                  include_unknown=True, countries=["BR"]),
    SearchFilters(countries=["BR"], work_models=["remote"],
                  seniorities=["senior"], exclude_languages=["de"],
                  include_unknown=False),
    SearchFilters(departments=["dados"], exclude_departments=["engenharia"]),
]


@pytest.fixture
def ids():
    with SessionLocal() as s:
        u = User(email=f"funnel-parity-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.flush()
        c = Company(user_id=u.id, source="greenhouse", identifier="acme", name="Acme")
        s.add(c)
        s.flush()
        rows = [Job(user_id=u.id, company_id=c.id, **j) for j in JOBS]
        s.add_all(rows)
        s.commit()
        out = {"uid": u.id, "ids": [r.id for r in rows]}
    yield out
    with SessionLocal() as s:
        s.execute(delete(Job).where(Job.user_id == out["uid"]))
        s.execute(delete(Company).where(Company.user_id == out["uid"]))
        s.execute(delete(User).where(User.id == out["uid"]))
        s.commit()


@pytest.mark.parametrize("case", range(len(CASES)))
def test_python_matches_sql(ids, case):
    f = CASES[case]
    with SessionLocal() as s:
        stmt = apply_filters(select(Job).where(Job.user_id == ids["uid"]), f)
        sql_ids = {j.id for j in s.scalars(stmt)}
        jobs = list(s.scalars(select(Job).where(Job.user_id == ids["uid"])))
    py_ids = {j.id for j in jobs if hard_filter(view_of_job(j), f)[0]}
    assert py_ids == sql_ids, (case, sorted(sql_ids ^ py_ids))
