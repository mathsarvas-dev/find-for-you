import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from rumo_core.db import SessionLocal
from rumo_core.matching.filters import SearchFilters
from rumo_core.models import SearchProfile, User
from rumo_core.sources.funnel import (
    FilterView,
    decide_intake,
    fold_like_unaccent,
    funnel_filters,
    hard_filter,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def view(**kw):
    base = dict(
        title="Backend Engineer", country="BR", work_model="remote", seniority="senior",
        language="en", department="Engineering", salary_known=False, salary_min=None,
        salary_max=None, salary_currency=None, salary_period=None,
    )
    base.update(kw)
    return FilterView(**base)


def test_reason_codes():
    cases = [
        (SearchFilters(countries=["DE"]), view(), "country"),
        (SearchFilters(work_models=["onsite"]), view(), "work_model"),
        (SearchFilters(seniorities=["junior"]), view(), "seniority"),
        (SearchFilters(languages=["pt"]), view(), "language"),
        (SearchFilters(departments=["sales"]), view(), "department"),
        (SearchFilters(exclude_countries=["BR"]), view(), "exclude_country"),
        (SearchFilters(exclude_work_models=["remote"]), view(), "exclude_work_model"),
        (SearchFilters(exclude_seniorities=["senior"]), view(), "exclude_seniority"),
        (SearchFilters(exclude_languages=["en"]), view(), "exclude_language"),
        (SearchFilters(exclude_departments=["engineering"]), view(), "exclude_department"),
        (SearchFilters(title_exclude=["backend"]), view(), "title_exclude"),
        (SearchFilters(salary_min=100000, salary_currency="USD", accept_no_salary=False),
         view(salary_known=True, salary_min=Decimal("50000"), salary_currency="USD",
              salary_period="year"), "salary"),
    ]
    for f, v, reason in cases:
        assert hard_filter(v, f) == (False, reason), reason
    assert hard_filter(view(), SearchFilters()) == (True, None)


def test_keywords_filter():
    v = view(title="Engenheiro de Dados", department=None)
    assert decide_intake(v, SearchFilters(), keywords=("dados",)) == ("full", None)
    assert decide_intake(v, SearchFilters(), keywords=("Dádos",)) == ("full", None)
    assert decide_intake(v, SearchFilters(), keywords=("python",)) == ("stub", "feed_keywords")
    assert decide_intake(v, SearchFilters(), keywords=("python",), text="Python e SQL") == (
        "full", None
    )


def test_decide_intake_none_filters_full():
    assert decide_intake(view(), None) == ("full", None)
    assert decide_intake(view(work_model="onsite"), None) == ("full", None)


def test_decide_intake_reason_from_filters():
    assert decide_intake(view(work_model="onsite"), SearchFilters(work_models=["remote"])) == (
        "stub", "work_model"
    )


def test_fold_like_unaccent():
    assert fold_like_unaccent("Straße ÉTÉ Øresund") == "strasse ete oresund"


@pytest.fixture
def user():
    with SessionLocal() as s:
        u = User(email=f"funnel-{uuid.uuid4().hex}@example.test")
        s.add(u)
        s.commit()
        uid = u.id
    yield uid
    with SessionLocal() as s:
        s.execute(delete(SearchProfile).where(SearchProfile.user_id == uid))
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def test_funnel_filters_no_profile_is_empty_filters(user):
    with SessionLocal() as s:
        assert funnel_filters(s, user) == SearchFilters()


def test_funnel_filters_invalid_returns_none(user):
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=user, name="Padrão", is_default=True,
                            filters={"countries": 5}))
        s.commit()
    with SessionLocal() as s:
        assert funnel_filters(s, user) is None


def test_funnel_filters_uses_default_profile(user):
    with SessionLocal() as s:
        s.add(SearchProfile(user_id=user, name="Outro", is_default=False,
                            filters={"work_models": ["onsite"]}))
        s.add(SearchProfile(user_id=user, name="Padrão", is_default=True,
                            filters={"work_models": ["remote"]}))
        s.commit()
    with SessionLocal() as s:
        assert funnel_filters(s, user).work_models == ["remote"]
