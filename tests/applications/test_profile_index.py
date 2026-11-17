from rumo_core.applications.profile_index import (
    evidence_block,
    full_profile_text,
    item_texts,
)
from rumo_core.profile.schema import (
    Achievement,
    Basics,
    CanonicalProfile,
    Certification,
    Education,
    Experience,
    Language,
    Note,
    Project,
    Skill,
)


def _profile():
    return CanonicalProfile(
        basics=Basics(
            name="Maria Souza", email="maria@pessoal.example", phone="+55 11 98765-4321",
            headline="Engenheira de Dados", links=["https://linkedin.example/in/maria"],
        ),
        summary="Engenheira com foco em pipelines.",
        experiences=[Experience(
            id="exp1", company="Acme", title="Engenheira de Dados",
            start="2019-03", end="2023-08", location="São Paulo",
            description="Pipelines em Airflow.",
            achievements=[Achievement(id="ach1", text="Reduzi o tempo do pipeline em 40% com Airflow")],
        )],
        education=[Education(id="edu1", institution="USP", degree="Bacharelado", field="Computação")],
        skills=[Skill(id="sk1", name="Python", level="avançado"), Skill(id="sk2", name="SQL")],
        languages=[Language(id="lg1", name="Inglês", level="fluente")],
        certifications=[Certification(id="ce1", name="AWS SAA", issuer="Amazon", date="2022")],
        projects=[Project(id="pr1", name="Painel", description="Dashboard interno", url="https://painel.example")],
        notes=[Note(id="nt1", topic="Viagens", text="Disponível para viagens")],
    )


def test_item_texts_cover_every_item_kind_and_pseudo_ids():
    texts = item_texts(_profile())
    for key in ("exp1", "ach1", "edu1", "sk1", "sk2", "lg1", "ce1", "pr1", "nt1", "summary", "skills"):
        assert key in texts and texts[key].strip(), key
    assert "40%" in texts["ach1"] and "Acme" in texts["ach1"]
    assert "Python" in texts["skills"] and "SQL" in texts["skills"]


def test_item_texts_never_include_contacts():
    blob = "\n".join(item_texts(_profile()).values()) + full_profile_text(_profile())
    for secret in ("maria@pessoal.example", "98765", "linkedin.example", "painel.example", "Maria Souza"):
        assert secret not in blob


def test_evidence_block_lists_achievement_ids():
    block = evidence_block(_profile(), ["exp1", "missing"])
    assert "[exp1]" in block
    assert "[ach1]" in block
    assert "missing" not in block
