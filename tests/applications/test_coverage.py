from rumo_core.applications.coverage import compute_coverage, line_covers, requirement_terms
from rumo_core.applications.lines import DocLine, document_status, locale_for, template_for
from rumo_core.applications.requirements import Requirement, Requirements

INDEX = {
    "exp1": "Engenheira de Dados na Acme. Pipelines em Airflow.",
    "ach1": "Reduzi o tempo do pipeline em 40% com Airflow (Engenheira de Dados na Acme)",
    "sk1": "Python",
    "sk2": "Kubernetes",
}


def _reqs():
    return Requirements(
        must=[
            Requirement(id="m1", text="Airflow"),
            Requirement(id="m2", text="Python"),
            Requirement(id="m3", text="Terraform"),
        ],
        nice=[Requirement(id="n1", text="Kubernetes"), Requirement(id="n2", text="Spark")],
    )


def _line(lid, text, refs, covers, violations=None):
    return DocLine(
        id=lid, section="bullet", text=text, source_refs=refs, origin="ai",
        covers=covers, violations=violations or [],
    )


def test_counts_must_and_nice_separately():
    lines = [
        _line("L1", "Usei Airflow", ["ach1"], ["m1"]),
        _line("L2", "Python todo dia", ["sk1"], ["m2"]),
        _line("L3", "Kubernetes em produção", ["sk2"], ["n1"]),
    ]
    cov = compute_coverage(_reqs(), lines, INDEX)
    assert cov["must"] == {"covered": 2, "total": 3}
    assert cov["nice"] == {"covered": 1, "total": 2}
    by_id = {i["id"]: i for i in cov["items"]}
    assert by_id["m1"]["covered"] and by_id["m1"]["line_ids"] == ["L1"]
    assert not by_id["m3"]["covered"] and by_id["m3"]["line_ids"] == []


def test_requirement_cited_only_by_invalid_line_not_covered():
    lines = [_line("L1", "Usei Terraform", ["exp1"], ["m3"], violations=["Termo que não está no seu perfil: Terraform"])]
    cov = compute_coverage(_reqs(), lines, INDEX)
    assert cov["must"]["covered"] == 0


def test_declared_cover_without_term_not_counted():
    # m1 is "Airflow"; the cited skill "Python" does not contain it
    lines = [_line("L1", "Trabalho em equipe", ["sk1"], ["m1"])]
    cov = compute_coverage(_reqs(), lines, INDEX)
    assert cov["must"]["covered"] == 0


def test_term_found_in_cited_item_counts():
    # the line text does not say "Airflow", the cited achievement does
    lines = [_line("L1", "Reduzi o tempo do pipeline em 40%", ["ach1"], ["m1"])]
    assert line_covers(lines[0], _reqs().must[0], INDEX)
    cov = compute_coverage(_reqs(), lines, INDEX)
    assert cov["must"]["covered"] == 1


def test_accent_and_case_ignored():
    req = Requirement(id="m1", text="Comunicação")
    line = _line("L1", "boa COMUNICACAO escrita", ["exp1"], ["m1"])
    assert line_covers(line, req, INDEX) is True
    assert requirement_terms(req) == ["Comunicação"]


def test_decisions_preserved_by_id():
    lines = [_line("L1", "Usei Airflow", ["ach1"], ["m1"])]
    cov = compute_coverage(_reqs(), lines, INDEX, decisions={"m3": "lack", "m1": "have"})
    by_id = {i["id"]: i for i in cov["items"]}
    assert by_id["m3"]["decision"] == "lack"
    assert by_id["m1"]["decision"] == "have"
    assert by_id["m2"]["decision"] is None


def test_document_status_needs_fix_when_any_violation():
    ok = _line("L1", "a", ["exp1"], [])
    bad = _line("L2", "b", ["exp1"], [], violations=["Número"])
    assert document_status([ok]) == "valid"
    assert document_status([ok, bad]) == "needs_fix"


def test_template_and_locale_from_job_fields():
    assert template_for("US") == "us"
    assert template_for("DE") == "de" and template_for("AT") == "de" and template_for("CH") == "de"
    assert template_for("AU") == "au"
    assert template_for("BR") == "br"
    assert template_for("FR") == "us" and template_for(None) == "us"
    assert locale_for("pt") == "pt" and locale_for("de") == "de"
    assert locale_for("unknown") == "en" and locale_for("en") == "en"
