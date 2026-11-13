"""Generate discovery candidates from catalog, profile and target companies."""

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rumo_core.discovery.catalog import load_catalog
from rumo_core.discovery.names import token_variants
from rumo_core.models import Company, DiscoveryCandidate, UserPreference
from rumo_core.profile.service import get_current_profile

MAX_TARGETS = 50
MAX_TARGET_LEN = 80


@dataclass(frozen=True)
class RefreshStats:
    """Outcome of one refresh.

    created: candidates inserted by this call.
    known: candidates newly marked ``known`` by this call (inserted as known,
    plus pending candidates that turned known). Known->pending moves and
    already-known rows are not counted, so an idempotent re-run reports 0.
    """

    created: int
    known: int


def _gather(session: Session, user_id: int) -> dict[str, tuple[str, str | None]]:
    """token -> (origin_kind, name_hint); priority target > profile > catalog
    (gathered in that order, first wins), deterministic."""
    found: dict[str, tuple[str, str | None]] = {}

    def add(token: str, kind: str, hint: str | None) -> None:
        found.setdefault(token, (kind, hint))

    pref = session.scalar(
        select(UserPreference)
        .where(UserPreference.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    targets = pref.target_companies if pref is not None else None
    if isinstance(targets, list):
        for item in targets[:MAX_TARGETS]:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if not name or len(name) > MAX_TARGET_LEN:
                continue
            for tok in token_variants(name):
                add(tok, "target", name[:255])
    profile = get_current_profile(session, user_id)
    data = profile.data if profile is not None else None
    experiences = data.get("experiences") if isinstance(data, dict) else None
    if isinstance(experiences, list):
        for exp in experiences:
            company = exp.get("company") if isinstance(exp, dict) else None
            if not isinstance(company, str) or not company.strip():
                continue
            name = company.strip()
            for tok in token_variants(name):
                add(tok, "profile", name[:255])

    for entry in load_catalog():
        add(entry.token, "catalog", entry.name)
    return found


def refresh_candidates(
    session: Session, user_id: int, *, source: str = "greenhouse"
) -> RefreshStats:
    """Insert missing candidates and reconcile pending/known ones.

    Pending profile/target candidates no longer generated are deleted; pending
    ones also carry the highest-priority origin. Candidates in state
    valid/invalid/rejected/known are never retired. Commits the
    session (including any pending work the caller had in it).
    """
    found = _gather(session, user_id)

    created = known = 0
    if found:
        companies = {
            ident.lower(): cid
            for cid, ident in session.execute(
                select(Company.id, Company.identifier).where(
                    Company.user_id == user_id, Company.source == source
                )
            )
        }
        rows = []
        for token, (kind, hint) in found.items():
            cid = companies.get(token)
            rows.append({
                "user_id": user_id, "source": source, "token": token,
                "name_hint": hint, "origin_kind": kind,
                "state": "known" if cid is not None else "pending",
                "company_id": cid,
            })
        stmt = (
            insert(DiscoveryCandidate)
            .values(rows)
            .on_conflict_do_nothing(
                constraint="uq_discovery_candidates_user_source_token"
            )
            .returning(DiscoveryCandidate.state)
        )
        states = session.execute(stmt).scalars().all()
        created = len(states)
        known = sum(1 for s in states if s == "known")

    # Retire pending profile/target candidates nobody generates any more and
    # upgrade pending ones whose token now comes from a higher-priority origin;
    # both guarded by state so concurrent valid/invalid/rejected changes win.
    cand = DiscoveryCandidate
    scope = (cand.user_id == user_id, cand.source == source)
    session.execute(
        delete(cand).where(
            *scope, cand.state == "pending",
            cand.origin_kind.in_(("profile", "target")),
            cand.token.not_in(list(found)),
        )
    )
    lower = {"target": ("profile", "catalog"), "profile": ("catalog",)}
    for kind, below in lower.items():
        tokens = [t for t, (k, _h) in found.items() if k == kind]
        if tokens:
            session.execute(
                update(cand)
                .where(*scope, cand.state == "pending",
                       cand.origin_kind.in_(below), cand.token.in_(tokens))
                .values(origin_kind=kind)
            )

    # Reconcile with set-based UPDATEs guarded by state, so a concurrent move
    # to valid/invalid/rejected is never overwritten.
    match = (
        select(Company.id)
        .where(
            Company.user_id == user_id,
            Company.source == source,
            func.lower(Company.identifier) == cand.token,
        )
        .order_by(Company.id)
        .limit(1)
        .correlate(cand)
    )
    newly = session.execute(
        update(cand)
        .where(*scope, cand.state == "pending", match.exists())
        .values(state="known", company_id=match.scalar_subquery())
        .returning(cand.id)
    ).all()
    known += len(newly)
    session.execute(
        update(cand)
        .where(
            *scope, cand.state == "known", match.exists(),
            cand.company_id.is_distinct_from(match.scalar_subquery()),
        )
        .values(company_id=match.scalar_subquery())
    )
    session.execute(
        update(cand)
        .where(*scope, cand.state == "known", ~match.exists())
        .values(state="pending", company_id=None)
    )
    session.commit()
    return RefreshStats(created=created, known=known)
