import json

import pytest

from rumo_core.sources.portals.gate import (
    MSG_BLOCKED,
    MSG_BLOCKED_BY_SITE,
    MSG_CONDITIONAL_OFF,
    MSG_PATH_NOT_ALLOWED,
    PortalUserState,
    check,
    load_policy,
    portal_for_url,
)


def _entry(**kw):
    base = {
        "id": "x", "name": "X", "kind": "portal", "status": "allowed", "checked_at": "2026-11-14",
        "evidence": {"urls": [], "summary": "s"}, "hosts": ["x.test"], "paths_allowed": [],
        "min_interval_seconds": 10, "daily_cap": 5,
    }
    base.update(kw)
    return base


def _policy_file(tmp_path, entries):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


def test_policy_file_is_valid():
    policy = load_policy()
    assert policy["indeed"].status == "blocked"
    assert policy["linkedin"].status == "blocked"
    assert policy["jobbol"].status == "blocked"
    assert policy["weworkremotely"].status == "blocked"
    assert policy["vagas"].status == "conditional"
    assert policy["catho"].status == "conditional"
    assert policy["remotive"].daily_cap == 4
    assert not any(p.status == "allowed" and p.kind == "portal" for p in policy.values())


def test_load_policy_rejects_bad_file(tmp_path):
    with pytest.raises(ValueError):
        load_policy(_policy_file(tmp_path, [_entry(status="maybe")]))
    with pytest.raises(ValueError):
        load_policy(_policy_file(tmp_path, [_entry(), _entry()]))


def test_portal_for_url():
    assert portal_for_url("https://www.vagas.com.br/vagas/v123/x").id == "vagas"
    assert portal_for_url("https://br.indeed.com/viewjob?jk=1").id == "indeed"
    assert portal_for_url("https://notvagas.com.br/") is None


def test_check_matrix(tmp_path):
    policy = load_policy(
        _policy_file(tmp_path, [
            _entry(id="blk", status="blocked", hosts=["blk.test"]),
            _entry(id="vag", status="conditional", kind="portal", hosts=["vag.test"], paths_allowed=["/vagas/v"]),
            _entry(id="cat", status="conditional", kind="portal", hosts=["cat.test"], paths_allowed=[]),
            _entry(id="ok", status="allowed", kind="feed", hosts=["ok.test"]),
        ])
    )
    on = PortalUserState(enabled=True)

    v = check("blk", "https://blk.test/x", policy=policy)
    assert (v.allowed, v.reason, v.message) == (False, "blocked", MSG_BLOCKED)
    assert check("blk", "https://blk.test/x", manual=True, policy=policy).allowed

    v = check("vag", "https://vag.test/vagas/v1/x", policy=policy)
    assert (v.allowed, v.reason, v.message) == (False, "conditional_off", MSG_CONDITIONAL_OFF)

    assert check("vag", "https://vag.test/vagas/v1/x", state=on, policy=policy).allowed
    v = check("vag", "https://vag.test/vagas/pesquisas", state=on, policy=policy)
    assert (v.reason, v.message) == ("path_not_allowed", MSG_PATH_NOT_ALLOWED)

    v = check("cat", "https://cat.test/vagas/x", state=on, policy=policy)
    assert v.reason == "path_not_allowed"

    v = check("ok", "https://ok.test/api", state=PortalUserState(blocked_by_site=True), policy=policy)
    assert (v.allowed, v.reason, v.message) == (False, "blocked_by_site", MSG_BLOCKED_BY_SITE)

    v = check("ok", "https://ok.test/api", policy=policy)
    assert v.allowed

    v = check("nope", policy=policy)
    assert (v.allowed, v.reason) == (False, "unknown_portal")
