"""Coverage of the job requirements by the document lines.

A requirement counts as covered only when a valid line declares it in `covers`
AND the line (or an item it cites) actually contains one of the requirement's
terms. The declaration alone is not enough: the model's claim is checked here.
"""

import re

from rumo_core.applications.lines import DocLine
from rumo_core.applications.requirements import Requirement, Requirements
from rumo_core.matching.ai_score import _STOPWORDS, _fold, _salient_terms


def requirement_terms(req: Requirement) -> list[str]:
    terms = _salient_terms(req.text)
    if terms:
        return terms
    words = [w for w in re.findall(r"\w+", req.text) if len(w) >= 4]
    content = [w for w in words if _fold(w) not in _STOPWORDS]
    # a requirement made only of generic vocabulary ("Comunicação") still needs a term
    return content or words


def _has_term(term: str, haystack_folded: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(_fold(term)) + r"(?!\w)"
    return re.search(pattern, haystack_folded) is not None


def line_covers(line: DocLine, req: Requirement, index: dict[str, str]) -> bool:
    cited = " ".join(index.get(ref, "") for ref in line.source_refs)
    haystack = _fold(f"{line.text} {cited}")
    return any(_has_term(term, haystack) for term in requirement_terms(req))


def compute_coverage(
    reqs: Requirements,
    lines: list[DocLine],
    index: dict[str, str],
    decisions: dict[str, str] | None = None,
) -> dict:
    decisions = decisions or {}
    items: list[dict] = []
    totals = {"must": [0, 0], "nice": [0, 0]}
    for kind, group in (("must", reqs.must), ("nice", reqs.nice)):
        for req in group:
            line_ids = [
                line.id
                for line in lines
                if not line.violations
                and req.id in line.covers
                and line_covers(line, req, index)
            ]
            covered = bool(line_ids)
            totals[kind][1] += 1
            totals[kind][0] += int(covered)
            items.append({
                "id": req.id,
                "kind": kind,
                "text": req.text,
                "covered": covered,
                "line_ids": line_ids,
                "decision": decisions.get(req.id),
            })
    return {
        "must": {"covered": totals["must"][0], "total": totals["must"][1]},
        "nice": {"covered": totals["nice"][0], "total": totals["nice"][1]},
        "items": items,
    }
