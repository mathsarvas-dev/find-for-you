import json
from pathlib import Path

import pytest

from rumo_core.applications.questions import (
    SENSITIVE_BANK_KEYS,
    is_sensitive,
    map_greenhouse,
    parse_pasted,
    sensitive_category,
)
from rumo_core.sources.base import RawQuestion

FIXTURE = Path(__file__).parents[1] / "fixtures/greenhouse/job_questions.json"


@pytest.mark.parametrize("label", [
    "Do you require visa sponsorship?",
    "Are you authorized to work in this country?",
    "Autorização de trabalho no país?",
    "Wie lautet Ihre Gehaltsvorstellung?",
    "What is your expected salary?",
    "Pretensão salarial",
    "Do you have a disability?",
    "Qual é o seu gênero?",
    "Ethnicity",
    "Have you ever been convicted of a criminal offense?",
    "Date of birth",
    "Qual sua idade?",
    "Are you open to relocation for this role?",
    "Are you a veteran?",
    "Sexual orientation",
    "First Name",
    "Telefone para contato",
])
def test_sensitive_list(label):
    assert is_sensitive(label) is True


@pytest.mark.parametrize("label", [
    "Why do you want to join us?",
    "Describe a project you are proud of",
    "Wie haben Sie von uns erfahren?",
])
def test_open_questions_are_not_sensitive(label):
    assert is_sensitive(label) is False


def test_age_word_boundary():
    assert is_sensitive("Can you manage a team?") is False
    assert is_sensitive("What is your age?") is True


def test_decline_option_is_sensitive():
    assert sensitive_category("Race", ["Asian", "Prefer not to say"]) == "race"
    assert is_sensitive("Tipo de pessoa", ["Opção A", "Prefiro não dizer"]) is True
    assert sensitive_category("Tipo", ["Opção A", "Opção B"]) is None


def test_compliance_and_demographic_always_sensitive():
    assert is_sensitive("Plain question", section="compliance") is True
    assert is_sensitive("Plain question", section="demographic") is True
    assert is_sensitive("Plain question", section="questions") is False


def test_bank_keys_cover_only_sensitive_categories():
    assert SENSITIVE_BANK_KEYS == {
        "salary": "salary_expectation",
        "work_authorization": "work_authorization",
        "visa": "work_authorization",
        "relocation": "relocation",
    }


def test_map_kinds():
    raw = [
        RawQuestion("Why?", "textarea", [], False, "questions"),
        RawQuestion("Resume/CV", "input_file", [], True, "questions"),
        RawQuestion("Open to in-person?", "multi_value_single_select", ["Yes", "No"], True, "questions"),
        RawQuestion("Pick one", "multi_value_single_select", ["A", "B"], False, "questions"),
        RawQuestion("hidden", "input_hidden", [], False, "questions"),
        RawQuestion("Race", "multi_value_single_select", ["X", "Y"], False, "demographic"),
    ]
    parsed = map_greenhouse(raw)
    kinds = {p.label: p.kind for p in parsed}
    assert kinds == {
        "Why?": "text", "Resume/CV": "file", "Open to in-person?": "bool",
        "Pick one": "choice", "Race": "choice",
    }
    assert next(p for p in parsed if p.label == "Race").sensitive is True
    assert next(p for p in parsed if p.label == "Pick one").options == ["A", "B"]


def test_parse_pasted_splits_on_blank_lines():
    text = "Por que a empresa?\nconte o motivo\n\n\nDescreva um projeto\n\n  \n"
    assert parse_pasted(text) == ["Por que a empresa? conte o motivo", "Descreva um projeto"]


def test_parse_pasted_caps_and_trims():
    many = "\n\n".join(f"Pergunta {i}" for i in range(40))
    assert len(parse_pasted(many)) == 30
    long = "x" * 1500
    assert len(parse_pasted(long)[0]) == 1000
    assert parse_pasted("   \n\n   ") == []


def test_fixture_questions_are_mapped():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = [RawQuestion(q["label"], q["fields"][0]["type"], [v["label"] for v in q["fields"][0].get("values", [])], bool(q.get("required")), "questions") for q in data["questions"]]
    parsed = {p.label: p for p in map_greenhouse(raw)}
    assert parsed["Why Anthropic?"].sensitive is False
    assert parsed["Do you require visa sponsorship?"].sensitive is True
    assert parsed["Are you open to relocation for this role?"].sensitive is True
    assert parsed["Resume/CV"].kind == "file"


# ---- drafting answers -------------------------------------------------------

import pytest as _pytest  # noqa: E402

from rumo_core.applications.questions import (  # noqa: E402
    MAX_BANK_ITEMS,
    AnswerInvalid,
    bank_reuse_ok,
    draft_answer,
)
from rumo_core.matching.ai_score import JobView  # noqa: E402
from rumo_core.profile.schema import Achievement, Basics, CanonicalProfile, Experience  # noqa: E402


class _FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        return schema.model_validate(self.answers.pop(0))


def _profile():
    return CanonicalProfile(
        basics=Basics(name="Ana Souza"),
        experiences=[Experience(id="exp1", company="Acme", title="Engenheira de Dados", start="2019-03",
                                description="Pipelines em Airflow.",
                                achievements=[Achievement(id="ach1", text="Reduzi o tempo do pipeline em 40% com Airflow")])],
    )


_JOB = JobView(title="Data Engineer", company_name="Globex", location_text=None, work_model=None,
               seniority=None, department=None, description="Pipelines de dados.")


def test_bank_reuse_keeps_number_and_currency():
    assert bank_reuse_ok("R$ 8.000 por mês", "R$ 8.000") is True
    assert bank_reuse_ok("US$ 8,000", "R$ 8.000") is False


def test_bank_number_converted_rejected():
    assert bank_reuse_ok("R$ 1.600", "R$ 8.000") is False


async def test_open_answer_validated_like_lines():
    bad = {"text": "Trabalhei 10 anos na Google", "source_refs": ["exp1"]}
    llm = _FakeLLM(bad, bad)
    with _pytest.raises(AnswerInvalid):
        await draft_answer(llm, "Por que nós?", _JOB, _profile(), ["exp1"], [], locale="pt")
    assert len(llm.calls) == 2


async def test_sensitive_bank_items_not_sent_to_llm():
    llm = _FakeLLM({"text": "Engenheira de dados com Airflow.", "source_refs": ["exp1"]})
    bank = [("salary_expectation", "pt", "R$ 8.000"), ("why_us", "pt", "Gosto do time")]
    await draft_answer(llm, "Por que nós?", _JOB, _profile(), ["exp1"], bank, locale="pt")
    blob = " ".join(m["content"] for m in llm.calls[0])
    assert "salary_expectation" not in blob and "R$ 8.000" not in blob
    assert "why_us" in blob


async def test_bank_capped_at_50_most_recent():
    llm = _FakeLLM({"text": "Resposta.", "source_refs": ["exp1"]})
    bank = [(f"key_{i}", "pt", f"valor {i}") for i in range(MAX_BANK_ITEMS + 10)]
    await draft_answer(llm, "Pergunta?", _JOB, _profile(), ["exp1"], bank, locale="pt")
    blob = " ".join(m["content"] for m in llm.calls[0])
    assert "key_49" in blob and "key_50" not in blob


async def test_bank_key_reused_only_when_numbers_match():
    # a non-sensitive key: the answer bank keeps the person's own wording and numbers
    llm = _FakeLLM({"bank_key": "hourly_rate", "text": "R$ 1.600 por hora", "source_refs": []},
                   {"bank_key": "hourly_rate", "text": "R$ 8.000 por hora", "source_refs": []})
    text, key = await draft_answer(llm, "Pergunta?", _JOB, _profile(), ["exp1"],
                                   [("hourly_rate", "pt", "R$ 8.000 por hora")], locale="pt")
    assert key == "hourly_rate" and text == "R$ 8.000 por hora"
    assert len(llm.calls) == 2  # the first draft changed the number and was retried
