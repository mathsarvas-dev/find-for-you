import pytest

from rumo_core.applications.draft import (
    DraftInvalid,
    build_fact_lines,
    draft_resume,
)
from rumo_core.applications.requirements import Requirement, Requirements
from rumo_core.applications.validator import R_NUMBER
from rumo_core.matching.ai_score import JobView
from rumo_core.profile.schema import (
    Achievement,
    Basics,
    CanonicalProfile,
    Certification,
    Education,
    Experience,
    Language,
    Skill,
)

JOB = JobView(
    title="Data Engineer", company_name="Globex", location_text="Remoto",
    work_model="remote", seniority=None, department=None,
    description="Pipelines de dados com Airflow.",
)
REQS = Requirements(must=[Requirement(id="m1", text="Airflow")], nice=[], keywords=[])


def _profile():
    return CanonicalProfile(
        basics=Basics(name="Maria Souza", email="maria@pessoal.example", phone="+55 11 98765-4321"),
        summary="Engenheira com foco em pipelines.",
        experiences=[
            Experience(
                id="exp1", company="Acme", title="Engenheira de Dados",
                start="2019-03", end="2023-08", description="Pipelines em Airflow.",
                achievements=[Achievement(id="ach1", text="Reduzi o tempo do pipeline em 40% com Airflow")],
            ),
            Experience(
                id="exp2", company="Beta", title="Analista", start="2016-01", end="",
                achievements=[Achievement(id="ach2", text="Montei relatórios mensais")],
            ),
        ],
        education=[Education(id="edu1", institution="USP", degree="Bacharelado", field="Computação", start="2015", end="2019")],
        skills=[Skill(id="sk1", name="Python"), Skill(id="sk2", name="SQL"), Skill(id="sk3", name="Airflow")],
        languages=[Language(id="lg1", name="Inglês", level="fluente")],
        certifications=[Certification(id="ce1", name="AWS SAA", issuer="Amazon", date="2022")],
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
        "summary": {"text": "Engenheira de dados com foco em Airflow.", "source_refs": ["summary"], "covers": ["m1"]},
        "experiences": [{"item_id": "exp1", "bullets": [
            {"text": "Reduzi o tempo do pipeline em 40% com Airflow", "source_refs": ["ach1"], "covers": ["m1"]},
        ]}],
        "projects": [],
        "skill_ids": ["sk3", "sk1"],
    }


def _ev():
    return {"m1": ["exp1", "summary"]}


async def test_facts_copied_from_profile():
    llm = FakeLLM(_good())
    lines = await draft_resume(llm, _profile(), JOB, REQS, _ev(), locale="pt")
    header = next(l for l in lines if l.section == "experience" and l.fields.get("company") == "Acme")
    assert header.origin == "fact" and header.fields["title"] == "Engenheira de Dados"
    assert header.fields["start"] == "2019-03" and header.fields["end"] == "2023-08"
    edu = next(l for l in lines if l.section == "education")
    assert edu.fields["institution"] == "USP"


async def test_llm_attempt_to_change_company_or_dates_ignored():
    bad = _good()
    bad["experiences"][0]["company"] = "Google"
    bad["experiences"][0]["start"] = "2010"
    lines = await draft_resume(FakeLLM(bad), _profile(), JOB, REQS, _ev(), locale="pt")
    blob = " ".join(l.text + str(l.fields) for l in lines)
    assert "Google" not in blob and "2010" not in blob
    header = next(l for l in lines if l.section == "experience" and l.fields.get("company") == "Acme")
    assert header.fields["start"] == "2019-03"


async def test_unknown_skill_ids_dropped_and_names_from_profile():
    good = _good()
    good["skill_ids"] = ["zzz", "sk3", "sk1", "sk3"]
    lines = await draft_resume(FakeLLM(good), _profile(), JOB, REQS, _ev(), locale="pt")
    skills = next(l for l in lines if l.section == "skills")
    assert skills.text == "Airflow, Python"
    assert skills.origin == "fact"


async def test_retry_sends_violations_then_succeeds():
    bad = _good()
    bad["experiences"][0]["bullets"][0]["text"] = "Reduzi o tempo do pipeline em 60% com Airflow"
    llm = FakeLLM(bad, _good())
    lines = await draft_resume(llm, _profile(), JOB, REQS, _ev(), locale="pt")
    assert len(llm.calls) == 2
    assert R_NUMBER.format(token="60%") in llm.calls[1][-1]["content"]
    assert any(l.text.startswith("Reduzi o tempo do pipeline em 40%") for l in lines)


async def test_second_failure_raises_draft_invalid():
    bad = _good()
    bad["experiences"][0]["bullets"][0]["text"] = "Reduzi o tempo do pipeline em 60% com Airflow"
    llm = FakeLLM(bad, bad)
    with pytest.raises(DraftInvalid) as exc:
        await draft_resume(llm, _profile(), JOB, REQS, _ev(), locale="pt")
    assert len(llm.calls) == 2
    assert exc.value.violations


async def test_prompt_has_no_contacts_or_name():
    llm = FakeLLM(_good())
    await draft_resume(llm, _profile(), JOB, REQS, _ev(), locale="pt")
    blob = " ".join(m["content"] for m in llm.calls[0])
    for secret in ("maria@pessoal.example", "98765", "Maria Souza"):
        assert secret not in blob


async def test_prompt_only_selected_evidence():
    llm = FakeLLM(_good())
    await draft_resume(llm, _profile(), JOB, REQS, {"m1": ["exp1"]}, locale="pt")
    blob = " ".join(m["content"] for m in llm.calls[0])
    assert "Reduzi o tempo do pipeline" in blob
    assert "Montei relatórios mensais" not in blob
    lines = build_fact_lines(_profile(), "pt")
    assert any(l.fields.get("company") == "Beta" for l in lines)


async def test_unchecked_covers_dropped():
    good = _good()
    good["summary"]["covers"] = ["m9", "m1"]
    good["summary"]["text"] = "Engenheira de dados."  # no term of "Airflow" in the line or its item
    good["summary"]["source_refs"] = ["summary"]
    lines = await draft_resume(FakeLLM(good), _profile(), JOB, REQS, _ev(), locale="pt")
    summary = next(l for l in lines if l.section == "summary")
    assert "m9" not in summary.covers and "m1" not in summary.covers


async def test_end_empty_rendered_as_locale_word():
    lines = build_fact_lines(_profile(), "de")
    header = next(l for l in lines if l.fields.get("company") == "Beta")
    assert header.fields["end"] == "heute"
