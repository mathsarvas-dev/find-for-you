from rumo_core.profile.schema import (
    Achievement,
    Basics,
    CanonicalProfile,
    Experience,
    Skill,
    assign_ids,
)


def test_partial_profile_valid():
    assert CanonicalProfile().experiences == []
    p = CanonicalProfile(basics=Basics(name="Ana"))
    assert p.basics.name == "Ana"
    assert CanonicalProfile.model_validate({"basics": {"name": "Ana"}}).summary == ""


def test_assign_ids_keeps_existing_and_fills_missing():
    p = CanonicalProfile(
        experiences=[
            Experience(id="keep1", company="X", achievements=[Achievement(text="a"), Achievement(id="ach1", text="b")]),
            Experience(company="Y"),
        ],
        skills=[Skill(name="Python")],
    )
    out = assign_ids(p)
    assert out.experiences[0].id == "keep1"
    assert out.experiences[0].achievements[1].id == "ach1"
    assert len(out.experiences[0].achievements[0].id) == 8
    assert len(out.experiences[1].id) == 8
    assert len(out.skills[0].id) == 8
    assert out.experiences[1].id != out.skills[0].id
