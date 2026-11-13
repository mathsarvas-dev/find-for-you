"""Generate discovery candidates from catalog, profile and target companies."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rumo_core.discovery.catalog import load_catalog
from rumo_core.discovery.names import token_variants
from rumo_core.models import Company, DiscoveryCandidate, UserPreference
from rumo_core.profile.schema import CanonicalProfile
from rumo_core.profile.service import get_current_profile


@dataclass(frozen=True)
class RefreshStats:
    created: int
    known: int


def _gather(session: Session, user_id: int) -> dict[str, tuple[str, str | None]]:
    """token -> (origin_kind, name_hint); first origin wins, deterministic."""
    found: dict[str, tuple[str, str | None]] = {}

    def add(token: str, kind: str, hint: str | None) -> None:
        found.setdefault(token, (kind, hint))

    for entry in load_catalog():
        add(entry.token, "catalog", entry.name)

    profile = get_current_profile(session, user_id)
    if profile is not None:
        try:
            parsed = CanonicalProfile.model_validate(profile.data)
        except Exception:
            parsed = None
        if parsed is not None:
            for exp in parsed.experiences:
                name = (exp.company or "").strip()
                if not name:
                    continue
                for tok in token_variants(name):
                    add(tok, "profile", name[:255])

    pref = session.get(UserPreference, user_id)
    if pref is not None and isinstance(pref.target_companies, list):
        for item in pref.target_companies:
            if not isinstance(item, str) or not item.strip():
                continue
            name = item.strip()
            for tok in token_variants(name):
                add(tok, "target", name[:255])
    return found


def refresh_candidates(
    session: Session, user_id: int, *, source: str = "greenhouse"
) -> RefreshStats:
    found = _gather(session, user_id)
    companies = {
        ident.lower(): cid
        for cid, ident in session.execute(
            select(Company.id, Company.identifier).where(
                Company.user_id == user_id, Company.source == source
            )
        )
    }

    created = known = 0
    if found:
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

    # Reconcile pre-existing pending/known candidates with current companies.
    existing = session.scalars(
        select(DiscoveryCandidate).where(
            DiscoveryCandidate.user_id == user_id,
            DiscoveryCandidate.source == source,
            DiscoveryCandidate.state.in_(("pending", "known")),
        )
    ).all()
    for cand in existing:
        cid = companies.get(cand.token)
        if cid is not None:
            if cand.state != "known":
                known += 1
            cand.state, cand.company_id = "known", cid
        elif cand.state == "known":
            cand.state, cand.company_id = "pending", None
    session.commit()
    return RefreshStats(created=created, known=known)
