"""
Resolver Service — DB-driven canonical part matching.

Addresses Section 1.5 of the checklist:
  - Candidate retrieval: exact MPN → manufacturer+MPN → alias → normalized text → fuzzy
  - Transparent scoring
  - Auto-match / soft-match / unresolved thresholds
  - Creates new canonical candidates from unresolved observations
  - Persists match decisions
  - Reuses canonical part keys from engine

This runs on platform-api side, NOT in the stateless engine.
"""
import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, text

from app.models.catalog import PartMaster, PartAlias
from app.models.bom import BOMPart

logger = logging.getLogger("resolver_service")

# ── Thresholds ──
AUTO_MATCH_THRESHOLD = 0.85   # Above this: auto-accept match
SOFT_MATCH_THRESHOLD = 0.50   # Between soft and auto: needs review
# Below soft: unresolved → create new candidate


def _clean_mpn(mpn: str) -> str:
    if not mpn:
        return ""
    return re.sub(r"[\s\-_]", "", mpn.strip().upper())


def _normalize_mfr(name: str) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = re.sub(r"\s+(inc\.?|llc|ltd\.?|corp\.?|co\.?|gmbh|ag|plc|sa|nv|bv)\s*$", "", s, flags=re.I)
    return s.strip()


def _similarity(a: str, b: str) -> float:
    """Simple Jaccard-like token similarity."""
    if not a or not b:
        return 0.0
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


def score_candidate(candidate: PartMaster, part: Dict[str, Any]) -> Dict[str, Any]:
    """Score a candidate match with transparent breakdown."""
    score = 0.0
    reasons = []

    part_mpn = _clean_mpn(part.get("mpn", ""))
    cand_mpn = _clean_mpn(candidate.mpn or "")
    part_mfr = _normalize_mfr(part.get("manufacturer", ""))
    cand_mfr = _normalize_mfr(candidate.manufacturer or "")
    part_desc = (part.get("description") or part.get("part_name") or "").lower()
    cand_desc = (candidate.description or "").lower()

    # MPN match (highest weight)
    if part_mpn and cand_mpn:
        if part_mpn == cand_mpn:
            score += 0.45
            reasons.append(f"MPN exact match: {part_mpn}")
        elif part_mpn in cand_mpn or cand_mpn in part_mpn:
            score += 0.25
            reasons.append(f"MPN partial: {part_mpn} ~ {cand_mpn}")

    # Manufacturer match
    if part_mfr and cand_mfr:
        if part_mfr == cand_mfr:
            score += 0.20
            reasons.append(f"Manufacturer match: {part_mfr}")
        elif part_mfr in cand_mfr or cand_mfr in part_mfr:
            score += 0.10
            reasons.append(f"Manufacturer partial: {part_mfr} ~ {cand_mfr}")

    # Category match
    part_cat = (part.get("category") or "").lower()
    cand_cat = (candidate.category or "").lower()
    if part_cat and cand_cat and part_cat == cand_cat:
        score += 0.10
        reasons.append(f"Category match: {part_cat}")

    # Domain match
    part_domain = (part.get("category") or "unknown").lower()
    cand_domain = (candidate.domain or "unknown").lower()
    if part_domain == cand_domain:
        score += 0.05
        reasons.append(f"Domain match: {part_domain}")

    # Description similarity
    desc_sim = _similarity(part_desc, cand_desc)
    if desc_sim > 0.3:
        desc_contrib = min(0.20, desc_sim * 0.25)
        score += desc_contrib
        reasons.append(f"Description similarity: {desc_sim:.2f}")

    # Material match
    part_mat = (part.get("material") or "").lower()
    cand_mat = (candidate.material or "").lower()
    if part_mat and cand_mat and (part_mat == cand_mat or part_mat in cand_mat):
        score += 0.05
        reasons.append(f"Material match: {part_mat}")

    # Canonical key exact match (highest possible)
    part_key = part.get("canonical_part_key", "")
    if part_key and part_key == candidate.canonical_part_key:
        score = max(score, 0.95)
        reasons.insert(0, f"Canonical key exact match")

    return {
        "candidate_id": candidate.id,
        "canonical_part_key": candidate.canonical_part_key,
        "score": round(min(1.0, score), 4),
        "reasons": reasons,
        "candidate_mpn": candidate.mpn,
        "candidate_manufacturer": candidate.manufacturer,
        "candidate_description": candidate.description,
    }


def find_candidates(db: Session, part: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
    """Multi-strategy candidate retrieval:
    1. Exact canonical key
    2. Exact MPN
    3. Manufacturer + MPN
    4. Alias lookup
    5. Normalized text match
    6. Category + description fuzzy
    """
    candidates: List[PartMaster] = []
    seen_ids = set()

    def _add(results):
        for r in results:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                candidates.append(r)

    canonical_key = part.get("canonical_part_key", "")
    mpn = _clean_mpn(part.get("mpn", ""))
    mfr = _normalize_mfr(part.get("manufacturer", ""))
    desc_text = (part.get("description") or part.get("part_name") or "").strip().lower()[:60]

    # 1. Exact canonical key
    if canonical_key:
        _add(db.query(PartMaster).filter(
            PartMaster.canonical_part_key == canonical_key
        ).limit(1).all())

    # 2. Exact MPN
    if mpn and len(mpn) >= 3:
        _add(db.query(PartMaster).filter(
            func.upper(func.replace(PartMaster.mpn, " ", "")) == mpn
        ).limit(3).all())

    # 3. Manufacturer + MPN combo
    if mpn and mfr:
        _add(db.query(PartMaster).filter(
            func.upper(func.replace(PartMaster.mpn, " ", "")) == mpn,
            func.lower(PartMaster.manufacturer).contains(mfr[:15])
        ).limit(3).all())

    # 4. Alias lookup
    if mpn and len(mpn) >= 3:
        alias_matches = (
            db.query(PartMaster)
            .join(PartAlias, PartAlias.part_master_id == PartMaster.id)
            .filter(PartAlias.normalized_value == mpn.lower())
            .limit(3)
            .all()
        )
        _add(alias_matches)

    if desc_text and len(desc_text) >= 5:
        alias_desc = (
            db.query(PartMaster)
            .join(PartAlias, PartAlias.part_master_id == PartMaster.id)
            .filter(
                PartAlias.alias_type == "description",
                PartAlias.normalized_value == desc_text[:40]
            )
            .limit(3)
            .all()
        )
        _add(alias_desc)

    # 5. Category + domain match (broader)
    category = (part.get("category") or "").lower()
    if category and category != "unknown" and len(candidates) < limit:
        _add(db.query(PartMaster).filter(
            PartMaster.category == category
        ).order_by(desc(PartMaster.observation_count)).limit(5).all())

    # Score all candidates
    scored = [score_candidate(c, part) for c in candidates]
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


def resolve_part(db: Session, part: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a single BOM part against the canonical master.
    Returns match decision with full explainability."""
    candidates = find_candidates(db, part, limit=5)

    if not candidates:
        return {
            "match_status": "unresolved",
            "match_score": 0,
            "match_reason": "No candidates found",
            "matched_master_id": None,
            "canonical_part_key": part.get("canonical_part_key", ""),
            "candidates": [],
            "action": "create_new",
        }

    best = candidates[0]

    if best["score"] >= AUTO_MATCH_THRESHOLD:
        return {
            "match_status": "auto_matched",
            "match_score": best["score"],
            "match_reason": "; ".join(best["reasons"]),
            "matched_master_id": best["candidate_id"],
            "canonical_part_key": best["canonical_part_key"],
            "candidates": candidates,
            "action": "accept",
        }
    elif best["score"] >= SOFT_MATCH_THRESHOLD:
        return {
            "match_status": "review_needed",
            "match_score": best["score"],
            "match_reason": "; ".join(best["reasons"]),
            "matched_master_id": best["candidate_id"],
            "canonical_part_key": best["canonical_part_key"],
            "candidates": candidates,
            "action": "review",
        }
    else:
        return {
            "match_status": "unresolved",
            "match_score": best["score"],
            "match_reason": f"Best candidate score {best['score']:.2f} below threshold",
            "matched_master_id": None,
            "canonical_part_key": part.get("canonical_part_key", ""),
            "candidates": candidates,
            "action": "create_new",
        }


def upsert_canonical_part(db: Session, part: Dict[str, Any]) -> PartMaster:
    """Create or update a canonical part master entry from a BOM observation.
    Used when a part is unresolved and needs a new canonical entry,
    or when a human approves a new part."""
    canonical_key = part.get("canonical_part_key", "")
    if not canonical_key:
        return None

    existing = db.query(PartMaster).filter(
        PartMaster.canonical_part_key == canonical_key
    ).first()

    if existing:
        # Update observation count
        existing.observation_count = (existing.observation_count or 0) + 1
        existing.updated_at = datetime.utcnow()
        # Update confidence if engine's is higher
        engine_conf = part.get("classification_confidence", 0)
        if engine_conf > float(existing.confidence or 0):
            existing.confidence = engine_conf
        db.flush()
        return existing

    # Create new entry
    category = (part.get("category") or "unknown").lower()
    pm = PartMaster(
        canonical_part_key=canonical_key,
        domain=category,
        category=category,
        procurement_class=part.get("procurement_class", "catalog_purchase"),
        description=part.get("description", ""),
        mpn=part.get("mpn", ""),
        manufacturer=part.get("manufacturer", ""),
        material=part.get("material", ""),
        material_grade=part.get("specs", {}).get("material_grade", ""),
        material_form=part.get("material_form", ""),
        specs=part.get("specs", {}),
        review_status=part.get("review_status", "auto"),
        confidence=part.get("classification_confidence", 0),
        source="observed",
        observation_count=1,
    )
    db.add(pm)
    db.flush()

    # Create aliases for this part
    _create_aliases(db, pm, part)

    return pm


def _create_aliases(db: Session, pm: PartMaster, part: Dict[str, Any]):
    """Create alias entries for a newly inserted PartMaster."""
    mpn = _clean_mpn(part.get("mpn", ""))
    if mpn and len(mpn) >= 3:
        _add_alias(db, pm.id, "mpn", part.get("mpn", ""), mpn.lower())

    desc_text = (part.get("description") or "").strip()
    if desc_text and len(desc_text) >= 5:
        _add_alias(db, pm.id, "description", desc_text, desc_text.lower()[:40])

    part_number = (part.get("part_number") or "").strip()
    if part_number and part_number != mpn and len(part_number) >= 3:
        _add_alias(db, pm.id, "supplier_pn", part_number, part_number.lower())


def _add_alias(db: Session, part_master_id: str, alias_type: str,
               alias_value: str, normalized_value: str):
    """Insert alias if not already present."""
    existing = db.query(PartAlias).filter(
        PartAlias.alias_type == alias_type,
        PartAlias.normalized_value == normalized_value,
    ).first()
    if not existing:
        db.add(PartAlias(
            part_master_id=part_master_id,
            alias_type=alias_type,
            alias_value=alias_value,
            normalized_value=normalized_value,
        ))
        db.flush()


def resolve_and_learn(db: Session, bom_parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve all parts in a BOM and learn from unresolved ones.
    Called after BOM creation to populate part_master from observations."""
    results = []
    for part in bom_parts:
        try:
            decision = resolve_part(db, part)

            if decision["action"] == "create_new":
                # Auto-create canonical entry from observation
                pm = upsert_canonical_part(db, part)
                if pm:
                    decision["matched_master_id"] = pm.id
                    decision["match_reason"] = "New canonical entry created from observation"
            elif decision["action"] == "accept":
                # Update observation count on matched master
                upsert_canonical_part(db, part)

            results.append(decision)
        except Exception as e:
            logger.warning(f"Resolve failed for {part.get('item_id', '?')}: {e}")
            results.append({
                "match_status": "error",
                "match_score": 0,
                "match_reason": str(e),
                "matched_master_id": None,
                "canonical_part_key": part.get("canonical_part_key", ""),
                "candidates": [],
                "action": "skip",
            })

    return results


def update_bom_parts_with_matches(db: Session, bom_id: str, match_results: List[Dict[str, Any]],
                                   bom_parts_dicts: List[Dict[str, Any]]):
    """Update BOMPart rows with resolver match results."""
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).order_by(BOMPart.source_row).all()

    for i, part in enumerate(parts):
        if i >= len(match_results):
            break
        mr = match_results[i]
        if mr.get("matched_master_id"):
            part.part_master_id = mr["matched_master_id"]
        if mr.get("match_status") == "review_needed":
            part.review_status = "needs_review"
        elif mr.get("match_status") == "auto_matched":
            part.review_status = "auto_matched"

    db.flush()
