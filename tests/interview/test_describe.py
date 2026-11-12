from rumo_core.interview.describe import describe_suggestion
from rumo_core.interview.suggestions import SuggestionDraft
from rumo_core.profile.schema import (
    Achievement,
    CanonicalProfile,
    Experience,
    Skill,
)


def _profile():
    return CanonicalProfile(
        summary="Dev backend",
        experiences=[
            Experience(id="e1", title="Dev", company="Acme", start="2019", end="2021",
                       description="Backend",
                       achievements=[Achievement(text="Migrei o banco"),
                                     Achievement(text="Reduzi custos")]),
            Experience(id="e2", title="QA", company="Beta"),
        ],
        skills=[Skill(name="Python"), Skill(name="SQL", level="avançado")],
    )


def test_append_achievement_with_and_without_existing():
    b, a = describe_suggestion(
        _profile(), SuggestionDraft(op="append_achievement", target_ref="e1", text="Cortei 30%"))
    assert "Reduzi custos" in b and "2" in b
    assert a == "Cortei 30%"
    b, _ = describe_suggestion(
        _profile(), SuggestionDraft(op="append_achievement", target_ref="e2", text="x"))
    assert "Nenhuma" in b


def test_set_experience_description():
    b, a = describe_suggestion(
        _profile(), SuggestionDraft(op="set_experience_description", target_ref="e1", text="Novo"))
    assert (b, a) == ("Backend", "Novo")
    b, _ = describe_suggestion(
        _profile(), SuggestionDraft(op="set_experience_description", target_ref="e2", text="N"))
    assert b == "(sem descrição)"


def test_set_experience_dates():
    b, a = describe_suggestion(
        _profile(), SuggestionDraft(op="set_experience_dates", target_ref="e1", start="2018"))
    assert b == "2019 – 2021"
    assert a == "2018 – 2021"
    b, a = describe_suggestion(
        _profile(), SuggestionDraft(op="set_experience_dates", target_ref="e2", start="2020"))
    assert b == "— – —"
    assert a == "2020 – —"


def test_summary_skill_preference_note():
    p = _profile()
    assert describe_suggestion(p, SuggestionDraft(op="set_summary", text="Novo")) == (
        "Dev backend", "Novo")
    assert describe_suggestion(None, SuggestionDraft(op="set_summary", text="Novo")) == (
        "—", "Novo")
    b, a = describe_suggestion(p, SuggestionDraft(op="add_skill", name="Go", level="básico"))
    assert b == "Python, SQL (avançado)"
    assert a == "Go (básico)"
    assert describe_suggestion(p, SuggestionDraft(op="add_skill", name="Go"))[1] == "Go"
    assert describe_suggestion(
        p, SuggestionDraft(op="set_preference", key="remote", value="sim")) == ("—", "remote: sim")
    assert describe_suggestion(p, SuggestionDraft(op="add_note", text="obs")) == ("—", "obs")


def test_set_headline_before_after():
    p = _profile()
    d = SuggestionDraft(op="set_headline", text="Engenheira de dados")
    assert describe_suggestion(p, d) == ("—", "Engenheira de dados")
    p.basics.headline = "Dev"
    assert describe_suggestion(p, d) == ("Dev", "Engenheira de dados")
    assert describe_suggestion(None, d) == ("—", "Engenheira de dados")


def test_preference_uses_pt_label_single_source():
    from rumo_core.interview.gaps import PREFERENCE_KEYS, PREFERENCE_LABELS

    assert set(PREFERENCE_LABELS) == set(PREFERENCE_KEYS)
    d = SuggestionDraft(op="set_preference", key="salary_expectation", value="R$ 8.000")
    assert describe_suggestion(_profile(), d) == (
        "—", "pretensão salarial: R$ 8.000")


def test_missing_target_or_profile_does_not_crash():
    d = SuggestionDraft(op="set_experience_description", target_ref="zz", text="Novo")
    assert describe_suggestion(_profile(), d) == ("(item removido)", "Novo")
    assert describe_suggestion(None, d) == ("(item removido)", "Novo")
    d = SuggestionDraft(op="set_experience_dates", target_ref="zz", start="2020", end="2022")
    assert describe_suggestion(_profile(), d) == ("(item removido)", "2020 – 2022")
    d = SuggestionDraft(op="append_achievement", target_ref="zz", text="x")
    assert describe_suggestion(None, d) == ("(item removido)", "x")
    assert describe_suggestion(None, SuggestionDraft(op="add_skill", name="Go"))[0] == "—"
