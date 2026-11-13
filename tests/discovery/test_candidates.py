import re
import uuid

import pytest
from sqlalchemy import delete, select

from rumo_core.db import SessionLocal
from rumo_core.discovery import candidates as cmod
from rumo_core.discovery.candidates import refresh_candidates
from rumo_core.discovery.catalog import CatalogEntry
from rumo_core.models import (
    Company, DiscoveryCandidate, Profile, User, UserPreference,
)

CATALOG = [CatalogEntry("alpha", "Alpha"), CatalogEntry("beta", "Beta")]


@pytest.fixture(autouse=True)
def small_catalog(monkeypatch):
    monkeypatch.setattr(cmod, "load_catalog", lambda *a, **k: list(CATALOG))


def _purge(s, uid):
    s.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid))
    s.execute(delete(UserPreference).where(UserPreference.user_id == uid))
    s.execute(delete(Company).where(Company.user_id == uid))
    s.execute(delete(Profile).where(Profile.user_id == uid))
    s.execute(delete(User).where(User.id == uid))
    s.commit()


@pytest.fixture
def session():
    with SessionLocal() as s:
        users = []
        for _ in range(2):
            u = User(email=f"disc-cand-{uuid.uuid4().hex}@example.test")
            s.add(u)
            users.append(u)
        s.commit()
        s.info["uid"] = users[0].id
        s.info["uid2"] = users[1].id
        yield s
        s.rollback()
        for u in users:
            _purge(s, u.id)


def _profile(s, uid, companies, version=1):
    s.add(Profile(
        user_id=uid, version=version, content_hash=uuid.uuid4().hex,
        data={"experiences": [{"company": c} for c in companies]},
    ))
    s.commit()


def _cands(s, uid):
    rows = s.scalars(
        select(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid)
    ).all()
    return {r.token: r for r in rows}


@pytest.mark.db
def test_catalog_candidates_created_once(session):
    uid = session.info["uid"]
    stats = refresh_candidates(session, uid)
    assert stats.created == 2
    c = _cands(session, uid)
    assert set(c) == {"alpha", "beta"}
    assert all(r.origin_kind == "catalog" and r.state == "pending" for r in c.values())
    assert refresh_candidates(session, uid).created == 0


@pytest.mark.db
def test_profile_experience_companies_become_candidates(session):
    uid = session.info["uid"]
    _profile(session, uid, ["Old Corp"], version=1)
    _profile(session, uid, ["Acme Widgets Inc", "  ", "Alpha"], version=2)
    refresh_candidates(session, uid)
    c = _cands(session, uid)
    assert "acmewidgets" in c and c["acmewidgets"].origin_kind == "profile"
    assert c["acmewidgets"].name_hint == "Acme Widgets Inc"
    assert "oldcorp" not in c
    # first origin wins: alpha stays catalog
    assert c["alpha"].origin_kind == "catalog"


@pytest.mark.db
def test_target_companies_become_candidates(session):
    uid = session.info["uid"]
    session.add(UserPreference(user_id=uid, target_companies=["Stripe", "Beta", 5, ""]))
    session.commit()
    refresh_candidates(session, uid)
    c = _cands(session, uid)
    assert c["stripe"].origin_kind == "target"
    assert c["beta"].origin_kind == "catalog"


@pytest.mark.db
def test_existing_company_marks_known_incl_paused(session):
    uid = session.info["uid"]
    co = Company(user_id=uid, source="greenhouse", identifier="alpha",
                 name="Alpha", active=False, origin="manual")
    session.add(co)
    session.commit()
    stats = refresh_candidates(session, uid)
    c = _cands(session, uid)
    assert c["alpha"].state == "known" and c["alpha"].company_id == co.id
    assert c["beta"].state == "pending"
    assert stats.known == 1
    # company removed -> back to pending
    session.delete(co)
    session.commit()
    refresh_candidates(session, uid)
    session.expire_all()
    c = _cands(session, uid)
    assert c["alpha"].state == "pending" and c["alpha"].company_id is None


@pytest.mark.db
def test_refresh_idempotent_and_keeps_states(session):
    uid = session.info["uid"]
    refresh_candidates(session, uid)
    for tok, st in (("alpha", "valid"), ("beta", "rejected")):
        _cands(session, uid)[tok].state = st
    session.commit()
    session.add(Company(user_id=uid, source="greenhouse", identifier="alpha", name="A"))
    session.commit()
    stats = refresh_candidates(session, uid)
    session.expire_all()
    c = _cands(session, uid)
    assert c["alpha"].state == "valid" and c["beta"].state == "rejected"
    assert (stats.created, stats.known) == (0, 0)
    assert len(c) == 2


@pytest.mark.db
def test_scoped_by_user(session):
    uid, uid2 = session.info["uid"], session.info["uid2"]
    session.add(Company(user_id=uid2, source="greenhouse", identifier="alpha", name="A"))
    session.commit()
    refresh_candidates(session, uid)
    assert _cands(session, uid2) == {}
    assert _cands(session, uid)["alpha"].state == "pending"


@pytest.mark.db
def test_no_profile_ok(session):
    uid = session.info["uid"]
    assert refresh_candidates(session, uid).created == 2


@pytest.mark.db
def test_hostile_names_create_only_safe_tokens(session):
    uid = session.info["uid"]
    _profile(session, uid, [
        "../../etc/passwd", "evil.com/x?y=1#z", "a" * 500, "\U0001f4a5",
        "x; DROP TABLE users;--",
    ])
    refresh_candidates(session, uid)
    rule = re.compile(r"[a-z0-9_-]{2,64}")
    toks = _cands(session, uid)
    for tok in toks:
        assert rule.fullmatch(tok), tok
    assert "etcpasswd" in toks


@pytest.mark.db
def test_reconcile_does_not_overwrite_concurrent_state_change(session, monkeypatch):
    uid = session.info["uid"]
    refresh_candidates(session, uid)
    session.add(Company(user_id=uid, source="greenhouse", identifier="alpha", name="A"))
    session.add(Company(user_id=uid, source="greenhouse", identifier="beta", name="B"))
    session.commit()
    real = cmod._gather

    def racing(sess, u):
        out = real(sess, u)
        with SessionLocal() as other:  # concurrent actor moves the rows
            for tok, st in (("alpha", "rejected"), ("beta", "valid")):
                row = other.scalar(select(DiscoveryCandidate).where(
                    DiscoveryCandidate.user_id == u, DiscoveryCandidate.token == tok))
                row.state = st
            other.commit()
        return out

    monkeypatch.setattr(cmod, "_gather", racing)
    stats = refresh_candidates(session, uid)
    session.expire_all()
    c = _cands(session, uid)
    assert c["alpha"].state == "rejected" and c["alpha"].company_id is None
    assert c["beta"].state == "valid" and c["beta"].company_id is None
    assert stats.known == 0


@pytest.mark.db
def test_second_run_pending_turns_known_and_is_counted(session):
    uid = session.info["uid"]
    assert refresh_candidates(session, uid).known == 0
    session.add(Company(user_id=uid, source="greenhouse", identifier="beta", name="B"))
    session.commit()
    stats = refresh_candidates(session, uid)
    assert (stats.created, stats.known) == (0, 1)
    assert refresh_candidates(session, uid).known == 0


@pytest.mark.db
def test_company_identifier_case_insensitive(session):
    uid = session.info["uid"]
    co = Company(user_id=uid, source="greenhouse", identifier="Alpha", name="A")
    session.add(co)
    session.commit()
    refresh_candidates(session, uid)
    c = _cands(session, uid)
    assert c["alpha"].state == "known" and c["alpha"].company_id == co.id


@pytest.mark.db
@pytest.mark.parametrize("targets", [
    {"a": "Stripe"}, "Stripe", 7, [],
    ["Stripe", None, {"x": 1}, 3, "x" * 81, " "],
])
def test_odd_target_companies(session, targets):
    uid = session.info["uid"]
    session.add(UserPreference(user_id=uid, target_companies=targets))
    session.commit()
    refresh_candidates(session, uid)
    toks = set(_cands(session, uid))
    expected = {"alpha", "beta"}
    if isinstance(targets, list) and "Stripe" in targets:
        expected.add("stripe")
    assert toks == expected


@pytest.mark.db
@pytest.mark.parametrize("data", [
    {}, {"experiences": None}, {"experiences": "x"}, ["not", "a", "dict"],
    {"experiences": [None, 3, {"company": None}, {"company": 5},
                     {"company": "Good Co"}]},
])
def test_odd_profile_data(session, data):
    uid = session.info["uid"]
    session.add(Profile(user_id=uid, version=1, content_hash="h", data=data))
    session.commit()
    refresh_candidates(session, uid)
    toks = set(_cands(session, uid))
    expected = {"alpha", "beta"}
    if isinstance(data, dict) and isinstance(data.get("experiences"), list):
        expected |= {"goodco", "good", "good-co"}
    assert toks == expected


@pytest.mark.db
def test_volume_bounded_queries_and_target_cap(session):
    from sqlalchemy import event
    uid = session.info["uid"]
    _profile(session, uid, [f"Firm{i} Inc" for i in range(50)])
    session.add(UserPreference(
        user_id=uid, target_companies=[f"Target{i}" for i in range(70)]))
    session.commit()
    stmts = []
    eng = session.get_bind()

    def count(conn, cursor, statement, *a):
        stmts.append(statement)

    event.listen(eng, "before_cursor_execute", count)
    try:
        refresh_candidates(session, uid)
    finally:
        event.remove(eng, "before_cursor_execute", count)
    assert len(stmts) <= 10
    toks = set(_cands(session, uid))
    assert "firm49" in toks and "target49" in toks and "target50" not in toks
