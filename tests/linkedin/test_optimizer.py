import pytest

from rumo_core.linkedin.optimizer import (
    VIEWPOINTS,
    OptimizationInvalid,
    optimize_profile,
)
from rumo_core.profile.schema import (
    Achievement,
    Basics,
    CanonicalProfile,
    Education,
    Experience,
    Skill,
)


def _profile():
    return CanonicalProfile(
        basics=Basics(name="Maria Souza", email="maria@pessoal.example", headline="Engenheira de dados"),
        summary="Engenheira com foco em pipelines.",
        experiences=[
            Experience(
                id="exp1", company="Acme", title="Engenheira de Dados", start="2019-03", end="2023-08",
                description="Pipelines em Airflow.",
                achievements=[Achievement(id="ach1", text="Reduzi o tempo do pipeline em 40% com Airflow")],
            ),
            Experience(id="exp2", company="Beta", title="Analista", start="2016-01", end=""),
        ],
        education=[Education(id="edu1", institution="USP", degree="Bacharelado", field="Computação")],
        skills=[Skill(id="sk1", name="Python"), Skill(id="sk2", name="SQL"), Skill(id="sk3", name="Airflow")],
    )


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        return schema.model_validate(self.responses.pop(0))


def _good():
    return {
        "headline": {"text": "Engenheira de dados | Airflow, Python e SQL", "source_refs": ["exp1", "sk1"]},
        "about": [{"text": "Engenheira de dados com foco em pipelines em Airflow.", "source_refs": ["summary", "exp1"]}],
        "experiences": [{"item_id": "exp1", "bullets": [
            {"text": "Reduzi o tempo do pipeline em 40% com Airflow", "source_refs": ["ach1"]},
        ]}, {"item_id": "ghost", "bullets": [{"text": "x", "source_refs": ["exp1"]}]}],
        "skill_ids": ["sk3", "sk1", "nope"],
        "viewpoints": [
            {"viewpoint": "recruiter", "notes": [{"text": "O título diz o cargo mas não a área de atuação."}]},
            {"viewpoint": "ats", "notes": [{"text": "Repita Airflow e SQL nas conquistas."},
                                           {"text": "Ganhe 300% mais visualizações com a palavra Spark."}]},
        ],
        "improvements": [
            {"priority": "low", "section": "skills", "text": "Ordene as habilidades por relevância."},
            {"priority": "high", "section": "experience", "text": "Quantifique a experiência na Beta.",
             "source_refs": ["exp2", "zzz"]},
            {"priority": "weird", "section": "mystery", "text": "Peça recomendações."},
        ],
    }


async def test_builds_draft_notes_and_priorities():
    llm = FakeLLM(_good())
    out = await optimize_profile(llm, _profile(), locale="pt")
    assert len(llm.calls) == 1
    assert out.headline.text.startswith("Engenheira de dados")
    assert [e.item_id for e in out.experiences] == ["exp1"]  # unknown experience ignored
    assert out.experiences[0].company == "Acme"  # facts come from the profile
    assert [s.id for s in out.skills] == ["sk3", "sk1"]
    assert [v.key for v in out.viewpoints] == [k for k, _ in VIEWPOINTS]
    ats = next(v for v in out.viewpoints if v.key == "ats")
    assert ats.notes == ["Repita Airflow e SQL nas conquistas."]  # the 300% claim is dropped
    assert [i.priority for i in out.improvements] == ["high", "medium", "low"]
    assert out.improvements[0].source_refs == ["exp2"]
    assert out.improvements[1].section == "general"
    assert out.rejected == []


async def test_invented_number_retries_then_succeeds():
    bad = _good()
    bad["headline"]["text"] = "Engenheira de dados com 12 anos de experiência"
    llm = FakeLLM(bad, _good())
    out = await optimize_profile(llm, _profile(), locale="pt")
    assert len(llm.calls) == 2
    assert "não passaram na verificação" in llm.calls[1][-1]["content"]
    assert out.headline is not None and out.rejected == []


async def test_section_still_invalid_after_retry_is_rejected_not_shown():
    bad = _good()
    bad["about"] = [{"text": "Trabalhei na Google com Kubernetes.", "source_refs": ["exp1"]}]
    llm = FakeLLM(bad, bad)
    out = await optimize_profile(llm, _profile(), locale="pt")
    assert out.about == []
    assert [r.section for r in out.rejected] == ["about"]
    assert out.headline is not None  # the other sections survive
    assert out.rejected[0].reasons


async def test_headline_over_limit_is_rejected():
    bad = _good()
    bad["headline"]["text"] = "Engenheira de dados " + "Airflow " * 40
    out = await optimize_profile(FakeLLM(bad, bad), _profile(), locale="pt")
    assert out.headline is None
    assert any("Título longo" in r for rj in out.rejected for r in rj.reasons)


async def test_unknown_source_is_rejected():
    bad = _good()
    bad["headline"]["source_refs"] = ["made-up"]
    out = await optimize_profile(FakeLLM(bad, bad), _profile(), locale="pt")
    assert out.headline is None and out.rejected[0].section == "headline"


async def test_empty_answer_raises():
    with pytest.raises(OptimizationInvalid):
        await optimize_profile(FakeLLM({}), _profile(), locale="pt")


async def test_prompt_wraps_profile_and_never_contains_contacts():
    llm = FakeLLM(_good())
    await optimize_profile(llm, _profile(), locale="en")
    system, user = llm.calls[0][0]["content"], llm.calls[0][1]["content"]
    assert "<perfil>" in user and "[exp1]" in user and "[ach1]" in user
    assert "maria@pessoal.example" not in user
    assert "English" in system
