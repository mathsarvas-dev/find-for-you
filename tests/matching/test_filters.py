import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters, apply_filters, fold_term
from rumo_core.models import Company, Job, User


def test_rejects_unknown_work_model():
    with pytest.raises(ValidationError):
        SearchFilters(work_models=["moon"])


def test_rejects_extra_and_bad_values():
    with pytest.raises(ValidationError):
        SearchFilters(foo=1)
    with pytest.raises(ValidationError):
        SearchFilters(seniorities=["god"])
    with pytest.raises(ValidationError):
        SearchFilters(languages=["fr"])
    with pytest.raises(ValidationError):
        SearchFilters(countries=["USA"])
    with pytest.raises(ValidationError):
        SearchFilters(departments=["x"] * 21)
    with pytest.raises(ValidationError):
        SearchFilters(salary_min=-1, salary_currency="USD")


def test_salary_min_requires_currency():
    with pytest.raises(ValidationError):
        SearchFilters(salary_min=1000)
    assert SearchFilters(salary_min=1000, salary_currency="USD")


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 1e309, 1e12 + 1])
def test_salary_min_must_be_finite_and_bounded(bad):
    with pytest.raises(ValidationError):
        SearchFilters(salary_min=bad, salary_currency="USD")
    assert SearchFilters(salary_min=1e12, salary_currency="USD").salary_min == 1e12


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_departments_reject_blank(bad):
    with pytest.raises(ValidationError):
        SearchFilters(departments=["Eng", bad])
    assert SearchFilters(departments=["Eng"]).departments == ["Eng"]


def test_countries_normalized():
    assert SearchFilters(countries=[" pt ", "de"]).countries == ["PT", "DE"]


def test_old_saved_filters_without_new_keys_still_valid():
    f = SearchFilters.model_validate({"countries": ["br"], "include_unknown": False})
    assert f.exclude_countries == []
    assert f.title_exclude == []


def test_exclusions_accept_valid_and_reject_unknown_values():
    assert SearchFilters(exclude_work_models=["onsite"]).exclude_work_models == ["onsite"]
    with pytest.raises(ValidationError):
        SearchFilters(exclude_work_models=["unknown"])
    with pytest.raises(ValidationError):
        SearchFilters(exclude_languages=["fr"])
    assert SearchFilters(exclude_countries=["pt"]).exclude_countries == ["PT"]


def test_include_and_exclude_same_value_is_error():
    with pytest.raises(ValidationError):
        SearchFilters(work_models=["remote"], exclude_work_models=["remote"])
    with pytest.raises(ValidationError):
        SearchFilters(departments=["Data"], exclude_departments=[" data "])


def test_title_exclude_limits():
    with pytest.raises(ValidationError):
        SearchFilters(title_exclude=[f"t{i}" for i in range(11)])
    with pytest.raises(ValidationError):
        SearchFilters(title_exclude=["  "])
    with pytest.raises(ValidationError):
        SearchFilters(title_exclude=["x" * 61])
    assert SearchFilters(title_exclude=["Plantão", "100%"]).title_exclude == [
        "Plantão", "100%"
    ]


def test_fold_term_escapes_wildcards():
    assert fold_term("Plantão") == "plantao"
    assert fold_term("100%_x\\") == "100\%\_x\\\\"


@pytest.fixture
def env():
    with SessionLocal() as s:
        user = User(email=f"filters-{uuid.uuid4().hex}@example.test")
        s.add(user)
        s.commit()
        c = Company(user_id=user.id, source="greenhouse", identifier="acme", name="Acme")
        s.add(c)
        s.commit()
        counter = iter(range(1000))

        def add(**kw):
            n = next(counter)
            kw.setdefault("title", f"t{n}")
            j = Job(
                user_id=user.id, company_id=c.id, external_id=str(n),
                url="https://example.test/j", description="d",
                raw={}, content_hash="h", fingerprint=f"f{n}", **kw,
            )
            s.add(j)
            s.commit()
            return j.id

        def run(filters):
            stmt = select(Job.id).where(Job.user_id == user.id)
            return set(s.scalars(apply_filters(stmt, filters)))

        yield add, run
        s.rollback()
        s.execute(delete(Job).where(Job.user_id == user.id))
        s.execute(delete(Company).where(Company.user_id == user.id))
        s.execute(delete(User).where(User.id == user.id))
        s.commit()


db = pytest.mark.db


@db
def test_country_filter_matches_normalized_country(env):
    add, run = env
    pt = add(country="PT")
    de = add(country="DE")
    null = add()
    assert run(SearchFilters(countries=["pt"], include_unknown=False)) == {pt}
    assert run(SearchFilters(countries=["pt"])) == {pt, null}
    assert de not in run(SearchFilters(countries=["pt"]))


@db
def test_unknown_work_model_kept_when_include_unknown(env):
    add, run = env
    remote = add(work_model="remote")
    onsite = add(work_model="onsite")
    unk = add()
    assert run(SearchFilters(work_models=["remote"])) == {remote, unk}
    assert onsite not in run(SearchFilters(work_models=["remote"]))


@db
def test_unknown_work_model_dropped_when_include_unknown_false(env):
    add, run = env
    remote = add(work_model="remote")
    add()
    f = SearchFilters(work_models=["remote"], include_unknown=False)
    assert run(f) == {remote}


@db
def test_seniority_and_language(env):
    add, run = env
    a = add(seniority="senior", language="en")
    add(seniority="junior", language="pt")
    u = add()
    f = SearchFilters(seniorities=["senior"], languages=["en"])
    assert run(f) == {a, u}
    f = SearchFilters(seniorities=["senior"], languages=["en"], include_unknown=False)
    assert run(f) == {a}


@db
def test_salary_filter_year_month_hour_normalization(env):
    add, run = env
    month = add(salary_known=True, salary_min=5000, salary_currency="USD", salary_period="month")
    hour = add(salary_known=True, salary_min=20, salary_currency="USD", salary_period="hour")
    year = add(salary_known=True, salary_min=40000, salary_currency="USD", salary_period="year")
    ymax = add(salary_known=True, salary_min=40000, salary_max=60000, salary_currency="USD", salary_period="year")
    f = SearchFilters(salary_min=50000, salary_currency="USD", include_unknown=False, accept_no_salary=False)
    assert run(f) == {month, ymax}
    assert hour not in run(f) and year not in run(f)


@db
def test_salary_other_currency_counts_as_unknown(env):
    add, run = env
    eur = add(salary_known=True, salary_min=1, salary_currency="EUR", salary_period="year")
    usd = add(salary_known=True, salary_min=90000, salary_currency="USD", salary_period="year")
    f = SearchFilters(salary_min=50000, salary_currency="USD", include_unknown=False)
    assert run(f) == {usd}
    f = SearchFilters(salary_min=50000, salary_currency="USD", include_unknown=True)
    assert eur in run(f)


@db
def test_no_salary_dropped_when_accept_no_salary_false(env):
    add, run = env
    nos = add()
    usd = add(salary_known=True, salary_min=90000, salary_currency="USD", salary_period="year")
    f = SearchFilters(salary_min=50000, salary_currency="USD", accept_no_salary=False)
    assert run(f) == {usd}
    f = SearchFilters(salary_min=50000, salary_currency="USD", accept_no_salary=True)
    assert run(f) == {usd, nos}


@db
def test_department_filter_is_case_insensitive_and_escapes_like(env):
    add, run = env
    eng = add(department="Engineering")
    pct = add(department="%")
    null = add()
    assert run(SearchFilters(departments=["engineering"], include_unknown=False)) == {eng}
    assert run(SearchFilters(departments=["%"], include_unknown=False)) == {pct}
    assert run(SearchFilters(departments=["engineering"])) == {eng, null}


@db
def test_empty_filters_change_nothing(env):
    add, run = env
    ids = {
        add(),
        add(country="PT", work_model="remote", department="X"),
        add(salary_known=True, salary_min=1, salary_currency="EUR", salary_period="hour"),
    }
    assert run(SearchFilters()) == ids


@db
def test_exclusion_keeps_unknown_regardless_of_include_unknown(env):
    add, run = env
    onsite = add(work_model="onsite")
    remote = add(work_model="remote")
    unk = add()
    for inc in (True, False):
        f = SearchFilters(exclude_work_models=["onsite"], include_unknown=inc)
        assert run(f) == {remote, unk}
        assert onsite not in run(f)
    de = add(country="DE")
    null_country = add()
    ids = run(SearchFilters(exclude_countries=["DE"], include_unknown=False))
    assert de not in ids and null_country in ids
    sales = add(department="Sales")
    null_dep = add(work_model="remote")
    for inc in (True, False):
        ids = run(SearchFilters(exclude_departments=["SALES"], include_unknown=inc))
        assert sales not in ids and null_dep in ids
        ids = run(SearchFilters(exclude_departments=["Sales"], include_unknown=inc))
        assert sales not in ids


@db
def test_title_exclude_ignores_accent_and_case(env):
    add, run = env
    add(title="Engenheiro de Dados (PLANTÃO)")
    keep = add(title="Data Engineer")
    assert run(SearchFilters(title_exclude=["plantao"])) == {keep}
    assert run(SearchFilters(title_exclude=["PLANTÃO"])) == {keep}


@db
def test_title_exclude_wildcards_are_literal(env):
    add, run = env
    pct = add(title="Bonus 100% remoto")
    thousand = add(title="Bonus 1000 remoto")
    us = add(title="a_b dev")
    axb = add(title="axb dev")
    assert run(SearchFilters(title_exclude=["100%"])) == {thousand, us, axb}
    assert run(SearchFilters(title_exclude=["a_b"])) == {pct, thousand, axb}


@db
def test_exclusions_combined_with_inclusions(env):
    add, run = env
    br = add(country="BR", seniority="senior")
    pt = add(country="PT", seniority="mid")
    add(country="PT", seniority="intern")
    add(country="DE", seniority="senior")
    add()
    f = SearchFilters(
        countries=["BR", "PT"], exclude_seniorities=["intern"], include_unknown=False
    )
    assert run(f) == {br, pt}
