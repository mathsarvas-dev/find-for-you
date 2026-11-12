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


def _ach(text):
    return SuggestionDraft(op="append_achievement", target_ref="e1", text=text)


@pytest.mark.parametrize("draft_text,answer", [
    ("Reduzi a latência em quarenta por cento", "melhorei a latência"),
    ("Liderei dez pessoas", "liderei um time"),
    ("Atendi mil clientes", "atendi muitos clientes"),
    ("Faturei 2 milhões", "faturei 2 reais"),
    ("Dobrei a receita", "aumentei a receita"),
    ("Triplicou o uso", "aumentou o uso"),
])
def test_lexicon_words_must_come_from_answer(draft_text, answer):
    assert _v(_ach(draft_text), answer) is not None


@pytest.mark.parametrize("draft_text,answer", [
    ("Reduzi a latência em quarenta por cento", "reduzi QUARENTA por cento"),
    ("Liderei dez pessoas", "Liderei DEZ pessoas"),
    ("Atendi mil clientes", "atendi mil clientes"),
    ("Faturei 2 milhões", "faturei 2 milhões"),
    ("Dobrei a receita", "dobrei a receita"),
    ("Triplicou o uso", "triplicou o uso"),
    ("Liderei três pessoas", "liderei tres pessoas"),  # accent folded
])
def test_lexicon_words_accepted_when_in_answer(draft_text, answer):
    assert _v(_ach(draft_text), answer) is None


def test_lexicon_is_word_bounded():
    assert _v(_ach("Liderei dez pessoas"), "liderei dezenas de pessoas") is not None


def test_month_names_and_current_markers_in_dates():
    g = _gap("experience_missing_dates")
    d = SuggestionDraft(op="set_experience_dates", target_ref="e1", start="mar/2018")
    assert _v(d, "comecei em 2018", gap=g) is not None
    assert _v(d, "comecei em mar/2018", gap=g) is None
    d = SuggestionDraft(op="set_experience_dates", target_ref="e1", start="2019", end="atual")
    assert _v(d, "comecei em 2019", gap=g) is not None
    assert _v(d, "comecei em 2019 e estou lá atualmente e atual", gap=g) is None
    d = SuggestionDraft(op="set_experience_dates", target_ref="e1", end="present")
    assert _v(d, "sai", gap=g) is not None


def test_dates_sources_exclude_description_and_achievements():
    g = _gap("experience_missing_dates")
    d = SuggestionDraft(op="set_experience_dates", target_ref="e1", end="2019")
    # description is "Backend em 2019" but dates may only use answer + existing start/end
    p = _profile()
    p.experiences[0].start = ""
    assert _v(d, "sai", gap=g, profile=p) is not None
    p.experiences[0].start = "2019"
    assert _v(d, "sai", gap=g, profile=p) is None


def test_token_boundary_130_does_not_satisfy_30():
    assert not numbers_supported("30%", ["cresci 130%"])
    assert not numbers_supported("130%", ["cresci 30%"])
    assert numbers_supported("30%", ["cresci 30%"])


def test_skill_match_is_word_bounded():
    g = _gap("missing_skills", None)
    for name, answer in [("Java", "uso JavaScript"), ("SQL", "uso MySQL"),
                         ("Go", "gosto de dados"), ("R", "trabalho com dados")]:
        assert _v(SuggestionDraft(op="add_skill", name=name), answer, gap=g) is not None
    for name, answer in [("C++", "programo em c++ há anos"), ("C#", "uso C# e Go."),
                         ("Go", "uso Go.")]:
        assert _v(SuggestionDraft(op="add_skill", name=name), answer, gap=g) is None


def test_skill_level_must_appear_in_answer():
    g = _gap("missing_skills", None)
    d = SuggestionDraft(op="add_skill", name="Docker", level="avançado")
    assert _v(d, "uso docker", gap=g) is not None
    assert _v(d, "uso docker em nível avançado", gap=g) is None
    d = SuggestionDraft(op="add_skill", name="Docker", level="5 anos")
    assert _v(d, "uso docker, 5 anos", gap=g) is None
    assert _v(d, "uso docker", gap=g) is not None
    assert _v(SuggestionDraft(op="add_skill", name="Docker"), "uso docker", gap=g) is None


def test_skill_empty_name_rejected():
    g = _gap("missing_skills", None)
    assert _v(SuggestionDraft(op="add_skill", name="  "), "uso docker", gap=g) is not None


def test_description_unsupported_number_and_empty():
    g = _gap("experience_without_description")
    d = SuggestionDraft(op="set_experience_description", target_ref="e1", text="Equipe de 12")
    assert _v(d, "equipe pequena", gap=g) is not None
    assert _v(d, "equipe de 12", gap=g) is None
    empty = SuggestionDraft(op="set_experience_description", target_ref="e1", text=" ")
    assert _v(empty, "x", gap=g) is not None


def test_summary_unsupported_number_and_empty():
    g = _gap("missing_summary", None)
    assert _v(SuggestionDraft(op="set_summary", text="10 anos de experiência"), "tenho experiência", gap=g) is not None
    assert _v(SuggestionDraft(op="set_summary", text="10 anos de experiência"), "tenho 10 anos", gap=g) is None
    assert _v(SuggestionDraft(op="set_summary", text=""), "x", gap=g) is not None


def test_note_unsupported_number():
    g = _gap("missing_skills", None)
    assert _v(SuggestionDraft(op="add_note", text="Ganhou 3 prêmios"), "ganhei prêmios", gap=g) is not None
    assert _v(SuggestionDraft(op="add_note", text="Ganhou 3 prêmios"), "ganhei 3 prêmios", gap=g) is None
