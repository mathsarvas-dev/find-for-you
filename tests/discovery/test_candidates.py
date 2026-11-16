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
    # profile outranks catalog
    assert c["alpha"].origin_kind == "profile"


@pytest.mark.db
def test_target_companies_become_candidates(session):
    uid = session.info["uid"]
    session.add(UserPreference(user_id=uid, target_companies=["Stripe", "Beta", 5, ""]))
    session.commit()
    refresh_candidates(session, uid)
    c = _cands(session, uid)
    assert c["stripe"].origin_kind == "target"
    assert c["beta"].origin_kind == "target"


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
@pytest.mark.parametrize("guard_case", ["pending_to_known", "known_to_pending"])
def test_reconcile_does_not_overwrite_concurrent_state_change(session, guard_case):
    """Race lands right before the first reconcile UPDATE on discovery_candidates."""
    from sqlalchemy import event, update

    uid = session.info["uid"]
    refresh_candidates(session, uid)
    if guard_case == "pending_to_known":
        # pending rows whose company exists: reconcile would mark them known
        session.add(Company(user_id=uid, source="greenhouse", identifier="alpha", name="A"))
        session.add(Company(user_id=uid, source="greenhouse", identifier="beta", name="B"))
        session.commit()
    else:
        # known rows whose company vanished: reconcile would reset them to pending
        for ident in ("alpha", "beta"):
            co = Company(user_id=uid, source="greenhouse", identifier=ident, name=ident)
            session.add(co)
            session.flush()
            session.execute(update(DiscoveryCandidate).where(
                DiscoveryCandidate.user_id == uid, DiscoveryCandidate.token == ident,
            ).values(state="known", company_id=co.id))
        session.commit()
        session.execute(delete(Company).where(Company.user_id == uid))
        session.commit()
    before = {t: (r.state, r.company_id) for t, r in _cands(session, uid).items()}
    assert before["alpha"][0] == before["beta"][0] == (
        "pending" if guard_case == "pending_to_known" else "known")

    fired = []

    def race(conn, cursor, statement, parameters, context, executemany):
        if fired or not statement.lstrip().upper().startswith("UPDATE DISCOVERY_CANDIDATES"):
            return
        fired.append(statement)
        with SessionLocal() as other:  # separate connection, commits immediately
            for tok, st in (("alpha", "rejected"), ("beta", "valid")):
                other.execute(update(DiscoveryCandidate).where(
                    DiscoveryCandidate.user_id == uid, DiscoveryCandidate.token == tok,
                ).values(state=st))
            other.commit()

    eng = session.get_bind()
    event.listen(eng, "before_cursor_execute", race)
    try:
        stats = refresh_candidates(session, uid)
    finally:
        event.remove(eng, "before_cursor_execute", race)
    assert fired, "race never injected"
    session.expire_all()
    c = _cands(session, uid)
    assert c["alpha"].state == "rejected" and c["beta"].state == "valid"
    assert c["alpha"].company_id == before["alpha"][1]
    assert c["beta"].company_id == before["beta"][1]
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


@pytest.mark.db
def test_origin_priority_target_over_profile_over_catalog(session):
    uid = session.info["uid"]
    refresh_candidates(session, uid)  # alpha, beta pending as catalog
    assert {r.origin_kind for r in _cands(session, uid).values()} == {"catalog"}
    _profile(session, uid, ["Alpha", "Beta"])
    session.add(UserPreference(user_id=uid, target_companies=["Beta"]))
    session.commit()
    refresh_candidates(session, uid)
    session.expire_all()
    c = _cands(session, uid)
    assert c["alpha"].origin_kind == "profile"
    assert c["beta"].origin_kind == "target"


@pytest.mark.db
def test_origin_upgrade_only_touches_pending(session):
    uid = session.info["uid"]
    refresh_candidates(session, uid)
    row = _cands(session, uid)["alpha"]
    row.state = "rejected"
    session.commit()
    session.add(UserPreference(user_id=uid, target_companies=["Alpha"]))
    session.commit()
    refresh_candidates(session, uid)
    session.expire_all()
    assert _cands(session, uid)["alpha"].origin_kind == "catalog"


@pytest.mark.db
def test_removed_target_pending_candidate_is_retired(session):
    uid = session.info["uid"]
    pref = UserPreference(user_id=uid, target_companies=["Acme", "Globex"])
    session.add(pref)
    session.commit()
    refresh_candidates(session, uid)
    assert {"acme", "globex"} <= set(_cands(session, uid))
    pref.target_companies = ["Globex"]
    session.commit()
    refresh_candidates(session, uid)
    session.expire_all()
    toks = set(_cands(session, uid))
    assert "acme" not in toks and "globex" in toks
    assert {"alpha", "beta"} <= toks  # catalog never deleted


@pytest.mark.db
def test_removed_profile_employer_is_retired(session):
    uid = session.info["uid"]
    _profile(session, uid, ["Initech", "Hooli"], version=1)
    refresh_candidates(session, uid)
    assert {"initech", "hooli"} <= set(_cands(session, uid))
    _profile(session, uid, ["Hooli"], version=2)
    refresh_candidates(session, uid)
    toks = set(_cands(session, uid))
    assert "initech" not in toks and "hooli" in toks


@pytest.mark.db
def test_retire_never_touches_non_pending(session):
    uid = session.info["uid"]
    names = ["Aa1", "Bb2", "Cc3", "Dd4"]
    session.add(UserPreference(user_id=uid, target_companies=names))
    session.commit()
    refresh_candidates(session, uid)
    for tok, st in (("aa1", "valid"), ("bb2", "invalid"), ("cc3", "rejected"),
                    ("dd4", "known")):
        _cands(session, uid)[tok].state = st
    session.commit()
    session.execute(delete(UserPreference).where(UserPreference.user_id == uid))
    session.commit()
    refresh_candidates(session, uid)
    session.expire_all()
    c = _cands(session, uid)
    assert c["aa1"].state == "valid" and c["bb2"].state == "invalid"
    assert c["cc3"].state == "rejected"
    assert "dd4" in c  # known without a company goes back to pending, not deleted


@pytest.mark.db
def test_retire_is_state_guarded_against_concurrent_change(session):
    from sqlalchemy import event, update

    uid = session.info["uid"]
    pref = UserPreference(user_id=uid, target_companies=["Acme"])
    session.add(pref)
    session.commit()
    refresh_candidates(session, uid)
    pref.target_companies = []
    session.commit()
    fired = []

    def race(conn, cursor, statement, parameters, context, executemany):
        if fired or not statement.lstrip().upper().startswith("DELETE FROM DISCOVERY_CANDIDATES"):
            return
        fired.append(1)
        with SessionLocal() as other:
            other.execute(update(DiscoveryCandidate).where(
                DiscoveryCandidate.user_id == uid, DiscoveryCandidate.token == "acme",
            ).values(state="rejected"))
            other.commit()

    eng = session.get_bind()
    event.listen(eng, "before_cursor_execute", race)
    try:
        refresh_candidates(session, uid)
    finally:
        event.remove(eng, "before_cursor_execute", race)
    assert fired
    session.expire_all()
    assert _cands(session, uid)["acme"].state == "rejected"


@pytest.mark.db
def test_refresh_per_source_tokens(session):
    uid = session.info["uid"]
    _profile(session, uid, ["Acme"])
    refresh_candidates(session, uid, source="lever")
    rows = session.scalars(select(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid)).all()
    assert {(r.source, r.token) for r in rows} == {("lever", "acme"), ("lever", "alpha"), ("lever", "beta")}
    refresh_candidates(session, uid, source="greenhouse")
    greenhouse = {r.token for r in session.scalars(select(DiscoveryCandidate).where(DiscoveryCandidate.user_id == uid, DiscoveryCandidate.source == "greenhouse")).all()}
    assert greenhouse == {"acme", "alpha", "beta"}


@pytest.mark.db
def test_feed_candidates_survive_refresh(session):
    uid = session.info["uid"]
    session.add(DiscoveryCandidate(user_id=uid, source="lever", token="zeta", origin_kind="feed", state="pending"))
    session.commit()
    refresh_candidates(session, uid, source="lever")
    assert session.scalar(select(DiscoveryCandidate.id).where(DiscoveryCandidate.user_id == uid, DiscoveryCandidate.token == "zeta")) is not None
