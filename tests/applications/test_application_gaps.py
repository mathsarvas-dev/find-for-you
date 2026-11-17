from rumo_core.applications.gaps import add_gap_to_profile, classify_gap_answer
from rumo_core.profile.schema import Basics, CanonicalProfile, Skill


def test_short_tool_name_is_skill():
    assert classify_gap_answer("Terraform") == "skill"
    assert classify_gap_answer("  Google Cloud  ") == "skill"


def test_sentence_is_note():
    assert classify_gap_answer("Já liderei uma equipe de cinco pessoas.") == "note"
    assert classify_gap_answer("Tenho experiência com infra, CI e deploy") == "note"
    assert classify_gap_answer("muito mais do que uma ferramenta isolada aqui") == "note"


def test_skill_not_duplicated():
    profile = CanonicalProfile(basics=Basics(), skills=[Skill(id="sk1", name="Terraform")])
    out = add_gap_to_profile(profile, "Terraform", "terraform")
    assert [s.name for s in out.skills] == ["Terraform"]
    assert profile.skills == out.skills  # the input is not changed


def test_note_gets_topic_and_text():
    profile = CanonicalProfile(basics=Basics())
    out = add_gap_to_profile(profile, "Liderança de equipe", "Liderei cinco pessoas por um ano.")
    assert out.notes[0].topic == "Liderança de equipe"
    assert out.notes[0].text == "Liderei cinco pessoas por um ano."
    assert out.skills == []
