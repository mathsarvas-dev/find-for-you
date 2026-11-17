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
