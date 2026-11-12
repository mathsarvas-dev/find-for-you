from rumo_core.interview.gaps import PREFERENCE_KEYS, detect_gaps
from rumo_core.profile.schema import Achievement, Basics, CanonicalProfile, Experience, Skill

ALL_PREFS = frozenset(PREFERENCE_KEYS)


def _exp(i, title="Dev", company="Acme", **kw):
    return Experience(id=i, title=title, company=company, start="2020", **kw)


def _complete(experiences=None):
    return CanonicalProfile(
        basics=Basics(name="A", headline="Engenheiro"),
        summary="Resumo",
        skills=[Skill(id="s1", name="Python")],
        experiences=experiences or [],
    )


def _kinds(gaps):
    return [g.kind for g in gaps]


def test_empty_profile_has_summary_skills_headline_and_preference_gaps():
    gaps = detect_gaps(CanonicalProfile())
    kinds = set(_kinds(gaps))
    assert {"missing_summary", "missing_skills", "missing_target_role"} <= kinds
    for key in PREFERENCE_KEYS:
        assert f"pref_{key}" in kinds
    assert len(gaps) == 3 + len(PREFERENCE_KEYS)


def test_experience_with_numbered_achievement_has_no_achievement_gap():
    exp = _exp("e1", achievements=[Achievement(id="a1", text="Reduzi o custo em 30%")])
    gaps = detect_gaps(_complete([exp]), ALL_PREFS)
    assert gaps == []


def test_experience_without_numbers_gets_gap_with_target_ref():
    exp = _exp("e1", achievements=[Achievement(id="a1", text="Melhorei o processo")])
    gaps = detect_gaps(_complete([exp]), ALL_PREFS)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.kind == "achievement_without_number"
    assert g.target_ref == "e1"
    assert g.id == "achievement_without_number:e1"
    assert "Dev @ Acme" in g.hint


def test_experience_description_only_gets_achievement_gap():
    exp = _exp("e1", description="Cuidei da plataforma")
    gaps = detect_gaps(_complete([exp]), ALL_PREFS)
    assert _kinds(gaps) == ["achievement_without_number"]


def test_experience_without_description_or_achievements():
    exp = Experience(id="e1", title="Dev", company="Acme", start="")
    gaps = detect_gaps(_complete([exp]), ALL_PREFS)
    assert sorted(_kinds(gaps)) == ["experience_missing_dates", "experience_without_description"]
    assert "achievement_without_number" not in _kinds(gaps)


def test_experience_with_empty_id_is_skipped():
    gaps = detect_gaps(_complete([Experience(title="Dev")]), ALL_PREFS)
    assert gaps == []


def test_hint_falls_back_when_title_and_company_empty():
    exp = Experience(id="e1", start="2020", description="x")
    gaps = detect_gaps(_complete([exp]), ALL_PREFS)
    assert gaps[0].hint


def test_answered_preferences_suppressed():
    answered = frozenset({"salary_expectation", "availability"})
    kinds = _kinds(detect_gaps(_complete(), answered))
    assert "pref_salary_expectation" not in kinds
    assert "pref_availability" not in kinds
    assert "pref_work_authorization" in kinds
    assert "pref_relocation" in kinds


def test_order_is_stable_and_deterministic():
    exps = [
        _exp("new", description="a"),
        _exp("old", description="b"),
    ]
    p = CanonicalProfile(experiences=exps)
    first = detect_gaps(p)
    assert first == detect_gaps(p)
    ach = [g.target_ref for g in first if g.kind == "achievement_without_number"]
    assert ach == ["new", "old"]
    pri = [g.priority for g in first]
    assert pri == sorted(pri)
    assert first[0].target_ref == "new"
    assert _kinds(first)[-len(PREFERENCE_KEYS):] == [f"pref_{k}" for k in sorted(PREFERENCE_KEYS)] or \
        all(k.startswith("pref_") for k in _kinds(first)[-len(PREFERENCE_KEYS):])


def test_complete_profile_only_preference_gaps_remain():
    exp = _exp("e1", achievements=[Achievement(id="a", text="Cortei 20% do tempo")])
    gaps = detect_gaps(_complete([exp]))
    assert len(gaps) == len(PREFERENCE_KEYS)
    assert all(g.kind.startswith("pref_") and g.target_ref is None for g in gaps)


def test_gap_ids_unique():
    exps = [_exp(f"e{i}", description="x") for i in range(4)]
    gaps = detect_gaps(CanonicalProfile(experiences=exps))
    ids = [g.id for g in gaps]
    assert len(ids) == len(set(ids))
