"""Quality measurement: labeled sample and precision@10 of the ranking.

Labels belong to (user, job); the sample is a deterministic function of the
user's seed and the current ranking, so it is stable until `new_seed`.
"""

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
    new = secrets.randbelow(_SEED_MAX)
    stmt = pg_insert(UserPreference).values(user_id=user_id, quality_seed=new)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserPreference.user_id], set_={"quality_seed": new}
    )
    session.execute(stmt)
    session.commit()
    return new


def build_sample(session: Session, user_id: int, *, model_version: str,
                 n: int = SAMPLE_SIZE) -> list[int]:
    version, used = usable_profile(session, user_id, model_version)
    if version is None or used == 0:
        return []
    seed = get_seed(session, user_id)
    qe = usable_query_embedding(session, user_id, model_version)
    page = rank_jobs(session, user_id, SearchFilters(), model_version=model_version,
                     status="open", limit=1_000_000, query_embedding=qe)
    ids = [it.job.id for it in page.items]
    top = sorted(ids[:TOP_POOL])
    rest = sorted(ids[TOP_POOL:])
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
