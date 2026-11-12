import pytest

from rumo_core.interview.gaps import Gap
from rumo_core.interview.suggestions import (
    SuggestionDraft,
    SuggestionTargetMissing,
    apply_suggestion,
    numbers_supported,
    validate_suggestion,
)
from rumo_core.profile.schema import Achievement, CanonicalProfile, Experience, Skill


def _profile():
    return CanonicalProfile(
        summary="",
        experiences=[Experience(
            id="e1", title="Dev", company="Acme", start="2019", description="Backend em 2019",
            achievements=[Achievement(id="a1", text="Migrei serviços")],
        )],
        skills=[Skill(id="s1", name="Python")],
    )


def _gap(kind="achievement_without_number", ref="e1"):
    return Gap(id=f"{kind}:{ref or '-'}", kind=kind, target_ref=ref, hint="h", priority=1)


def _v(draft, answer, gap=None, profile=None):
    return validate_suggestion(
        draft, answer=answer, profile=profile or _profile(), gap=gap or _gap())


def test_numbers_must_come_from_answer():
    d = SuggestionDraft(op="append_achievement", target_ref="e1", text="Reduzi 40% da latência")
    assert _v(d, "melhorei a performance") is not None


def test_numbers_from_answer_or_target_item_accepted():
    d = SuggestionDraft(op="append_achievement", target_ref="e1", text="Reduzi a latência em 30%")
    assert _v(d, "reduzi a latência em 30%") is None
    d2 = SuggestionDraft(op="append_achievement", target_ref="e1", text="Atuei em 2019 no backend")
    assert _v(d2, "atuei no backend") is None  # 2019 is in the target item


def test_thousand_separators_normalized():
    assert numbers_supported("atendi 1.000 clientes", ["atendi 1000 clientes"])
    assert numbers_supported("atendi 1000 clientes", ["atendi 1.000 clientes"])
    assert numbers_supported("cresceu 2,5 vezes", ["cresceu 2.5 vezes"])
    assert not numbers_supported("atendi 2000", ["atendi 1000"])
    assert numbers_supported("sem numeros", [])


def test_unknown_target_rejected():
    d = SuggestionDraft(op="append_achievement", target_ref="zzz", text="Fiz algo")
    assert _v(d, "fiz algo", gap=_gap(ref="zzz")) is not None
    d2 = SuggestionDraft(op="append_achievement", target_ref=None, text="Fiz algo")
    assert _v(d2, "fiz algo") is not None


def test_target_must_match_gap_target():
    p = _profile()
    p.experiences.append(Experience(id="e2", title="X"))
    d = SuggestionDraft(op="append_achievement", target_ref="e2", text="Fiz algo")
    assert _v(d, "fiz algo", profile=p) is not None


def test_preference_key_whitelist():
    g = _gap("pref_availability", None)
    ok = SuggestionDraft(op="set_preference", key="availability", value="imediata")
    assert _v(ok, "imediata", gap=g) is None
    bad = SuggestionDraft(op="set_preference", key="favorite_color", value="azul")
    assert _v(bad, "azul", gap=g) is not None
    other = SuggestionDraft(op="set_preference", key="relocation", value="sim")
    assert _v(other, "sim", gap=g) is not None  # key differs from gap
    empty = SuggestionDraft(op="set_preference", key="availability", value=" ")
    assert _v(empty, "x", gap=g) is not None
    num = SuggestionDraft(op="set_preference", key="availability", value="30 dias")
    assert _v(num, "imediata", gap=g) is not None


def test_new_skill_must_appear_in_answer():
    g = _gap("missing_skills", None)
    ok = SuggestionDraft(op="add_skill", name="Docker")
    assert _v(ok, "uso docker todo dia", gap=g) is None
    bad = SuggestionDraft(op="add_skill", name="Kubernetes")
    assert _v(bad, "uso docker todo dia", gap=g) is not None


def test_op_must_fit_gap():
    d = SuggestionDraft(op="set_summary", text="Resumo")
    assert _v(d, "Resumo") is not None  # experience gap
    assert _v(d, "Resumo", gap=_gap("missing_summary", None)) is None
    assert _v(d, "Resumo", gap=_gap("missing_target_role", None)) is None
    assert _v(d, "Resumo", gap=_gap("missing_skills", None)) is not None
    note = SuggestionDraft(op="add_note", text="algo")
    assert _v(note, "algo", gap=_gap("missing_skills", None)) is None
    assert _v(SuggestionDraft(op="add_note", text=""), "algo") is not None


def test_dates_need_value_and_support():
    g = _gap("experience_missing_dates")
    assert _v(SuggestionDraft(op="set_experience_dates", target_ref="e1"), "x", gap=g) is not None
    ok = SuggestionDraft(op="set_experience_dates", target_ref="e1", start="2018-03")
    assert _v(ok, "comecei em 2018-03", gap=g) is None
    bad = SuggestionDraft(op="set_experience_dates", target_ref="e1", start="2015")
    assert _v(bad, "comecei antes", gap=g) is not None


def test_append_achievement_adds_to_target_and_appends_note():
    p = _profile()
    d = SuggestionDraft(op="append_achievement", target_ref="e1", text="Reduzi 30%")
    out = apply_suggestion(p, d, answer="  reduzi 30%  ", gap=_gap())
    assert [a.text for a in out.experiences[0].achievements] == ["Migrei serviços", "Reduzi 30%"]
    assert out.experiences[0].achievements[-1].id == ""
    assert out.notes[-1].topic == "achievement_without_number"
    assert out.notes[-1].text == "reduzi 30%"
    assert out.notes[-1].id == ""


def test_set_experience_dates():
    d = SuggestionDraft(op="set_experience_dates", target_ref="e1", start="2018", end="2020")
    out = apply_suggestion(_profile(), d, answer="a", gap=_gap("experience_missing_dates"))
    assert (out.experiences[0].start, out.experiences[0].end) == ("2018", "2020")
    d2 = SuggestionDraft(op="set_experience_dates", target_ref="e1", end="2021")
    out2 = apply_suggestion(_profile(), d2, answer="a", gap=_gap("experience_missing_dates"))
    assert (out2.experiences[0].start, out2.experiences[0].end) == ("2019", "2021")


def test_set_summary_description_skill_note():
    out = apply_suggestion(_profile(), SuggestionDraft(op="set_summary", text="Novo"),
                           answer="a", gap=_gap("missing_summary", None))
    assert out.summary == "Novo"
    out = apply_suggestion(_profile(), SuggestionDraft(
        op="set_experience_description", target_ref="e1", text="Desc"), answer="a", gap=_gap())
    assert out.experiences[0].description == "Desc"
    out = apply_suggestion(_profile(), SuggestionDraft(op="add_skill", name="Docker", level="básico"),
                           answer="docker", gap=_gap("missing_skills", None))
    assert out.skills[-1].name == "Docker" and out.skills[-1].level == "básico"
    out = apply_suggestion(_profile(), SuggestionDraft(op="add_note", text="t"),
                           answer="resp", gap=_gap("missing_skills", None))
    assert len(out.notes) == 1  # add_note: only the answer note, no duplicate


def test_target_missing_raises():
    d = SuggestionDraft(op="append_achievement", target_ref="nope", text="x")
    with pytest.raises(SuggestionTargetMissing):
        apply_suggestion(_profile(), d, answer="x", gap=_gap())


def test_set_preference_does_not_change_profile():
    p = _profile()
    d = SuggestionDraft(op="set_preference", key="availability", value="imediata")
    out = apply_suggestion(p, d, answer="imediata", gap=_gap("pref_availability", None))
    assert out == p and out is not p


def test_apply_is_pure():
    p = _profile()
    before = p.model_dump()
    apply_suggestion(p, SuggestionDraft(op="append_achievement", target_ref="e1", text="x"),
                     answer="x", gap=_gap())
    assert p.model_dump() == before
