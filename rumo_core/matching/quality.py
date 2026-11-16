"""Quality measurement: labeled sample and precision@10 of the ranking.

Labels belong to (user, job); the sample is a deterministic function of the
user's seed and the current ranking, so it is stable until `new_seed`.
"""

import math
import random
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from rumo_core.matching.filters import SearchFilters
from rumo_core.matching.ranking import (
    rank_jobs,
    top_candidate_ids,
    usable_profile,
    usable_query_embedding,
)
from rumo_core.models import JobLabel, UserPreference

TARGET = 0.6
K = 10
MIN_LABELS = 30
MIN_EACH = 10
SAMPLE_SIZE = 40
TOP_POOL = 50

_SEED_MAX = 2**31 - 1


def precision_at_k(labels: Mapping[int, str], ranked_ids: Sequence[int],
                   k: int = 10) -> float:
    top = list(ranked_ids)[:k]
    if not top:
        return 0.0
    good = sum(1 for jid in top if labels.get(jid) == "good")
    return good / min(k, len(ranked_ids))


def get_seed(session: Session, user_id: int) -> int:
    seed = session.scalar(
        select(UserPreference.quality_seed).where(UserPreference.user_id == user_id)
    )
    if seed is not None:
        return seed
    # Only fills a missing seed; never touches other columns or an existing seed.
    new = secrets.randbelow(_SEED_MAX)
    stmt = pg_insert(UserPreference).values(user_id=user_id, quality_seed=new)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserPreference.user_id],
        set_={"quality_seed": new},
        where=UserPreference.quality_seed.is_(None),
    )
    session.execute(stmt)
    session.commit()
    session.expire_all()
    return session.scalar(
        select(UserPreference.quality_seed).where(UserPreference.user_id == user_id)
    )


def new_seed(session: Session, user_id: int) -> int:
    """Replace the seed (always a different value); labels are untouched."""
    old = session.scalar(
        select(UserPreference.quality_seed).where(UserPreference.user_id == user_id)
    )
    new = secrets.randbelow(_SEED_MAX)
    if new == old:
        new = (new + 1) % _SEED_MAX
    stmt = pg_insert(UserPreference).values(user_id=user_id, quality_seed=new)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserPreference.user_id], set_={"quality_seed": new}
    )
    session.execute(stmt)
    session.commit()
    return new


def build_sample(session: Session, user_id: int, *, model_version: str,
                 n: int = SAMPLE_SIZE) -> list[int]:
    """Deterministic (per seed) sample of open, vectorized jobs: n//2 from the
    TOP_POOL best in the current order, n//2 from the rest (a half with too few
    jobs is not topped up from the other). An odd n yields n//2 per half."""
    if n < 0:
        raise ValueError("n deve ser >= 0.")
    version, used = usable_profile(session, user_id, model_version)
    if version is None or used == 0:
        return []
    seed = get_seed(session, user_id)
    qe = usable_query_embedding(session, user_id, model_version)
    # Full rows only for the top pool; the rest is an id-only query.
    page = rank_jobs(session, user_id, SearchFilters(), model_version=model_version,
                     status="open", limit=TOP_POOL, query_embedding=qe)
    top = sorted(it.job.id for it in page.items)
    top_set = set(top)
    every = top_candidate_ids(session, user_id, SearchFilters(),
                              model_version=model_version, n=2**31 - 1,
                              status="open", query_embedding=qe)
    rest = sorted(jid for jid, _ in every if jid not in top_set)
    rng = random.Random(seed)
    half = n // 2
    picked = rng.sample(top, min(half, len(top)))
    picked += rng.sample(rest, min(half, len(rest)))
    rng.shuffle(picked)
    return picked


@dataclass
class Misranked:
    job_id: int
    title: str
    label: str
    position: int


@dataclass
class QualityReport:
    status: Literal["insuficiente", "dentro_da_meta", "abaixo_da_meta"]
    labeled: int
    good: int
    bad: int
    unranked: int
    missing: list[str]
    precision_at_10: float | None
    precision_vec_at_10: float | None
    target: float = TARGET
    misranked: list[Misranked] = field(default_factory=list)


def _plural(n: int, one: str, many: str) -> str:
    return f"falta 1 {one}" if n == 1 else f"faltam {n} {many}"


def evaluate(session: Session, user_id: int, *, model_version: str) -> QualityReport:
    # Labeled jobs that are not ranked (no embedding, or status 'duplicate')
    # count as `unranked`, not toward the minimums.
    labels = {
        jid: lab for jid, lab in session.execute(
            select(JobLabel.job_id, JobLabel.label).where(JobLabel.user_id == user_id)
        )
    }
    items = []
    if labels:
        qe = usable_query_embedding(session, user_id, model_version)
        page = rank_jobs(session, user_id, SearchFilters(),
                         model_version=model_version, status="all",
                         limit=len(labels), query_embedding=qe,
                         only_ids=list(labels))
        items = page.items
    ranked_labels = {it.job.id: labels[it.job.id] for it in items}
    labeled = len(ranked_labels)
    good = sum(1 for v in ranked_labels.values() if v == "good")
    bad = labeled - good
    unranked = len(labels) - labeled

    missing: list[str] = []
    if labeled < MIN_LABELS:
        missing.append(_plural(MIN_LABELS - labeled, "rótulo", "rótulos"))
    if good < MIN_EACH:
        missing.append(_plural(MIN_EACH - good, "vaga boa", "vagas boas"))
    if bad < MIN_EACH:
        missing.append(_plural(MIN_EACH - bad, "vaga ruim", "vagas ruins"))
    if missing:
        return QualityReport(
            status="insuficiente", labeled=labeled, good=good, bad=bad,
            unranked=unranked, missing=missing, precision_at_10=None,
            precision_vec_at_10=None, target=TARGET, misranked=[],
        )

    current = [it.job.id for it in items]  # already (ai desc, vec desc, id)
    funnel = [it.job.id for it in sorted(items, key=lambda it: (-it.vec_score, it.job.id))]
    p = precision_at_k(ranked_labels, current, K)
    pv = precision_at_k(ranked_labels, funnel, K)
    misranked = [
        Misranked(job_id=it.job.id, title=it.job.title,
                  label=ranked_labels[it.job.id], position=pos)
        for pos, it in enumerate(items, start=1)
        if (ranked_labels[it.job.id] == "good" and pos > K)
        or (ranked_labels[it.job.id] == "bad" and pos <= K)
    ]
    return QualityReport(
        status="dentro_da_meta" if p >= TARGET else "abaixo_da_meta",
        labeled=labeled, good=good, bad=bad, unranked=unranked, missing=[],
        precision_at_10=p, precision_vec_at_10=pv, target=TARGET,
        misranked=misranked,
    )


FLOOR_KEEP = 0.95


def floor_from_scores(scores: Sequence[float], keep: float = FLOOR_KEEP) -> float | None:
    """Highest cut that keeps `keep` of the scores (the lowest of them is dropped).

    None when there are fewer than MIN_EACH scores: not enough to suggest one.
    Rounded DOWN to 3 decimals so the cut never removes more than planned.
    """
    if len(scores) < MIN_EACH:
        return None
    ordered = sorted(float(x) for x in scores)
    k = math.floor((1 - keep) * len(ordered) + 1e-9)
    return math.floor(ordered[k] * 1000) / 1000


def suggest_floor(session: Session, user_id: int, *, model_version: str) -> float | None:
    """Cut suggested from the jobs the user marked 'good' (never applied by itself)."""
    good = [
        job_id for (job_id,) in session.execute(
            select(JobLabel.job_id).where(JobLabel.user_id == user_id, JobLabel.label == "good")
        ).all()
    ]
    if not good:
        return None
    ranked = top_candidate_ids(
        session, user_id, SearchFilters(), model_version=model_version, n=len(good),
        status="all", intakes=("full", "gated"), only_ids=good,
        query_embedding=usable_query_embedding(session, user_id, model_version),
    )
    return floor_from_scores([score for _, score in ranked])

