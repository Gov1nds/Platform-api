"""
Resolver Service — DB-driven canonical part matching.

Addresses Section 1.5 of the checklist:
  - Candidate retrieval: exact MPN → manufacturer+MPN → alias → normalized text → fuzzy
  - Transparent scoring
  - Auto-match / soft-match / unresolved thresholds
  - Creates new canonical candidates from unresolved observations
  - Persists match decisions
  - Reuses canonical part keys from engine

Upgrades (v2):
  - Calibration hook (_apply_calibration) wired into score_candidate
  - Fallback-parse penalty + dedup-confidence boost in calibration
  - Full alias expansion preserved in find_candidates
  - _extract_and_store_attributes guarded by _INDEX_KEYS whitelist (no junk columns)
  - resolve_and_learn: full error handling + rich decision metadata + alias creation on accept/review
  - update_bom_parts_with_matches: canonical_part_key kept in sync
  - _record_observation: every field explicitly mapped; safe-defaults for all optionals
  - upsert_canonical_part: spec merge + alias creation + confidence promotion preserved

This runs on platform-api side, NOT in the stateless engine.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, text

from app.models.catalog import PartMaster, PartAlias, PartObservation, PartAttribute
from app.models.bom import BOMPart

logger = logging.getLogger("resolver_service")


# ══════════════════════════════════════════════════════════════════════════════
# THRESHOLDS  (calibratable — swap for DB-backed config in future)
# ══════════════════════════════════════════════════════════════════════════════

AUTO_MATCH_THRESHOLD = 0.85   # Above this: auto-accept
SOFT_MATCH_THRESHOLD = 0.50   # Between soft and auto: needs review
# Below soft → unresolved → create new canonical candidate


# ══════════════════════════════════════════════════════════════════════════════
# CALIBRATION HOOK
# ══════════════════════════════════════════════════════════════════════════════

def _apply_calibration(score: float, part: Dict[str, Any]) -> float:
    """
    Post-scoring calibration hook.

    Currently rule-based; replace body with ML model call or DB-driven
    lookup table without changing the call-site signature.

    Rules applied:
      - Fallback-parsed rows carry more noise → small penalty.
      - High-dedup-count rows are seen many times → small confidence boost.
      - All other fields are available for future rules.
    """
    # Type guard: a corrupt score must not propagate through arithmetic
    if not isinstance(score, (int, float)):
        return 0.0

    try:
        # Penalise rows that were parsed via the fallback extractor
        if part.get("parse_status") == "fallback":
            score *= 0.95

        # Boost if this part text has been seen repeatedly (dedup evidence)
        dup_count = int(part.get("duplicate_count") or 1)
        if dup_count > 3:
            score = min(1.0, score + 0.02)

        # Placeholder: failure_metadata could carry domain-specific signals
        # meta = part.get("failure_metadata") or {}

    except Exception:
        pass  # Never let calibration crash the pipeline

    return min(1.0, score)


# ══════════════════════════════════════════════════════════════════════════════
# NORMALIZATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _clean_mpn(mpn: str) -> str:
    if not mpn:
        return ""
    return re.sub(r"[\s\-_]", "", mpn.strip().upper())


def _normalize_mfr(name: str) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = re.sub(
        r"\s+(inc\.?|llc|ltd\.?|corp\.?|co\.?|gmbh|ag|plc|sa|nv|bv)\s*$",
        "",
        s,
        flags=re.I,
    )
    return s.strip()


def _similarity(a: str, b: str) -> float:
    """Jaccard token similarity."""
    if not a or not b:
        return 0.0
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_alias(value: Any) -> str:
    return _clean_text(value)


# ══════════════════════════════════════════════════════════════════════════════
# SPEC & FEEDBACK SCORING
# ══════════════════════════════════════════════════════════════════════════════

def _spec_overlap_score(
    part: Dict[str, Any], candidate: PartMaster
) -> Tuple[float, List[str]]:
    part_specs = part.get("specs") or {}
    cand_specs = candidate.specs or {}

    if not isinstance(part_specs, dict) or not isinstance(cand_specs, dict):
        return 0.0, []

    score = 0.0
    reasons: List[str] = []

    for key, pval in part_specs.items():
        if pval in (None, "", [], {}):
            continue
        cval = cand_specs.get(key)
        if cval in (None, "", [], {}):
            continue

        p = _clean_text(pval)
        c = _clean_text(cval)

        if not p or not c:
            continue

        if p == c:
            score += 0.04
            reasons.append(f"Spec exact: {key}")
        elif p in c or c in p:
            score += 0.02
            reasons.append(f"Spec partial: {key}")

    return min(0.15, score), reasons


def _feedback_boost(candidate: PartMaster) -> float:
    """
    Boost score for candidates that have been seen and confirmed many times.
    Capped so it cannot alone push a weak match over threshold.
    """
    obs = float(candidate.observation_count or 0)
    conf = float(candidate.confidence or 0)

    boost = min(0.10, obs * 0.002)
    boost += min(0.08, conf * 0.08)
    return min(0.15, boost)


# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score_candidate(candidate: PartMaster, part: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score a single PartMaster candidate against an incoming BOM part dict.

    Signal weights (approximate):
      MPN exact        +0.45
      MPN partial      +0.25
      Manufacturer     +0.20 / +0.10
      Category         +0.10
      Domain           +0.05
      Description sim  up to +0.20
      Material         +0.05
      Spec overlap     up to +0.15
      Feedback boost   up to +0.15
      Calibration      ±small
      Canonical key    floor 0.95
    """
    score = 0.0
    reasons: List[str] = []

    # ── Normalise inputs ──
    part_mpn = _clean_mpn(part.get("mpn", ""))
    cand_mpn = _clean_mpn(candidate.mpn or "")

    part_mfr = _normalize_mfr(part.get("manufacturer", ""))
    cand_mfr = _normalize_mfr(candidate.manufacturer or "")

    part_desc = _clean_text(
        part.get("description")
        or part.get("part_name")
        or part.get("standard_text")
        or ""
    )
    cand_desc = _clean_text(candidate.description or "")

    part_cat = _clean_text(part.get("category") or "")
    cand_cat = _clean_text(candidate.category or "")

    part_domain = _clean_text(part.get("category") or "unknown")
    cand_domain = _clean_text(candidate.domain or "unknown")

    # ── MPN ──
    if part_mpn and cand_mpn:
        if part_mpn == cand_mpn:
            score += 0.45
            reasons.append(f"MPN exact match: {part_mpn}")
        elif part_mpn in cand_mpn or cand_mpn in part_mpn:
            score += 0.25
            reasons.append(f"MPN partial: {part_mpn} ~ {cand_mpn}")

    # ── Manufacturer ──
    if part_mfr and cand_mfr:
        if part_mfr == cand_mfr:
            score += 0.20
            reasons.append(f"Manufacturer match: {part_mfr}")
        elif part_mfr in cand_mfr or cand_mfr in part_mfr:
            score += 0.10
            reasons.append(f"Manufacturer partial: {part_mfr} ~ {cand_mfr}")

    # ── Category ──
    if part_cat and cand_cat and part_cat == cand_cat:
        score += 0.10
        reasons.append(f"Category match: {part_cat}")

    # ── Domain ──
    if part_domain == cand_domain:
        score += 0.05
        reasons.append(f"Domain match: {part_domain}")

    # ── Description similarity ──
    desc_sim = _similarity(part_desc, cand_desc)
    if desc_sim > 0.3:
        desc_contrib = min(0.20, desc_sim * 0.25)
        score += desc_contrib
        reasons.append(f"Description similarity: {desc_sim:.2f}")

    # ── Material ──
    part_mat = _clean_text(part.get("material") or "")
    cand_mat = _clean_text(candidate.material or "")
    if part_mat and cand_mat and (
        part_mat == cand_mat or part_mat in cand_mat or cand_mat in part_mat
    ):
        score += 0.05
        reasons.append(f"Material match: {part_mat}")

    # ── Spec overlap ──
    spec_boost, spec_reasons = _spec_overlap_score(part, candidate)
    if spec_boost:
        score += spec_boost
        reasons.extend(spec_reasons)

    # ── Feedback / calibration boost ──
    cal_boost = _feedback_boost(candidate)
    if cal_boost:
        score += cal_boost
        reasons.append(
            f"Calibration boost: obs={candidate.observation_count or 0},"
            f" confidence={candidate.confidence or 0}"
        )

    # ── Canonical key shortcut ──
    part_key = part.get("canonical_part_key", "")
    if part_key and part_key == candidate.canonical_part_key:
        score = max(score, 0.95)
        reasons.insert(0, "Canonical key exact match")

    # ── Apply post-scoring calibration ──
    score = _apply_calibration(score, part)

    return {
        "candidate_id": candidate.id,
        "canonical_part_key": candidate.canonical_part_key,
        "score": round(min(1.0, score), 4),
        "reasons": reasons,
        # Preserve candidate fields for upstream explainability
        "candidate_mpn": candidate.mpn,
        "candidate_manufacturer": candidate.manufacturer,
        "candidate_description": candidate.description,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE RETRIEVAL  (multi-pass: exact → alias → category fallback)
# ══════════════════════════════════════════════════════════════════════════════

def find_candidates(
    db: Session, part: Dict[str, Any], limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Multi-pass candidate search.  Passes in priority order:
      1. Canonical part key (if engine already resolved)
      2. Exact MPN match in PartMaster
      3. Part-number alias lookup
      4. MPN + manufacturer combined
      5. MPN alias lookup
      6. Description alias lookup
      7. Category popularity fallback
    """
    candidates: List[PartMaster] = []
    seen_ids: set = set()

    def _add(results):
        for r in results:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                candidates.append(r)

    canonical_key = part.get("canonical_part_key", "")
    mpn = _clean_mpn(part.get("mpn", ""))
    part_number = _clean_mpn(part.get("part_number", ""))
    mfr = _normalize_mfr(part.get("manufacturer", ""))
    desc_text = _clean_text(
        part.get("description")
        or part.get("part_name")
        or part.get("standard_text")
        or ""
    )[:80]

    # Pass 1 — canonical key (highest confidence)
    if canonical_key:
        _add(
            db.query(PartMaster)
            .filter(PartMaster.canonical_part_key == canonical_key)
            .limit(1)
            .all()
        )

    # Pass 2 — exact MPN in PartMaster
    if mpn and len(mpn) >= 3:
        _add(
            db.query(PartMaster)
            .filter(
                func.upper(func.replace(PartMaster.mpn, " ", "")) == mpn
            )
            .limit(3)
            .all()
        )

    # Pass 3 — part_number via alias table
    if part_number and len(part_number) >= 3 and part_number != mpn:
        alias_matches = (
            db.query(PartMaster)
            .join(PartAlias, PartAlias.part_master_id == PartMaster.id)
            .filter(PartAlias.normalized_value == part_number.lower())
            .limit(3)
            .all()
        )
        _add(alias_matches)

    # Pass 4 — MPN + manufacturer combined
    if mpn and mfr:
        _add(
            db.query(PartMaster)
            .filter(
                func.upper(func.replace(PartMaster.mpn, " ", "")) == mpn,
                func.lower(PartMaster.manufacturer).contains(mfr[:15]),
            )
            .limit(3)
            .all()
        )

    # Pass 5 — MPN in alias table (catches cross-reference MPNs)
    if mpn and len(mpn) >= 3:
        alias_matches = (
            db.query(PartMaster)
            .join(PartAlias, PartAlias.part_master_id == PartMaster.id)
            .filter(PartAlias.normalized_value == mpn.lower())
            .limit(3)
            .all()
        )
        _add(alias_matches)

    # Pass 6 — description/name alias lookup
    if desc_text and len(desc_text) >= 5:
        alias_desc = (
            db.query(PartMaster)
            .join(PartAlias, PartAlias.part_master_id == PartMaster.id)
            .filter(
                PartAlias.alias_type.in_(("description", "name")),
                PartAlias.normalized_value == desc_text,
            )
            .limit(3)
            .all()
        )
        _add(alias_desc)

    # Pass 7 — category popularity fallback (only if still thin on candidates)
    category = _clean_text(part.get("category") or "")
    if category and category != "unknown" and len(candidates) < limit:
        _add(
            db.query(PartMaster)
            .filter(PartMaster.category == category)
            .order_by(desc(PartMaster.observation_count))
            .limit(5)
            .all()
        )

    scored = [score_candidate(c, part) for c in candidates]
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


# ══════════════════════════════════════════════════════════════════════════════
# PART RESOLUTION  (single part)
# ══════════════════════════════════════════════════════════════════════════════

def resolve_part(db: Session, part: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve a single BOM part against the canonical master.
    Returns a full decision dict with explainability fields.
    """
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


# ══════════════════════════════════════════════════════════════════════════════
# CANONICAL PART UPSERT
# ══════════════════════════════════════════════════════════════════════════════

def upsert_canonical_part(db: Session, part: Dict[str, Any]) -> Optional[PartMaster]:
    """
    Insert or update a PartMaster row from an observed BOM part.

    On update:
      - Increments observation_count
      - Promotes confidence if engine reports a higher value
      - Fills in missing fields (never overwrites existing data)
      - Merges specs (additive, never destructive)
      - Creates any new aliases implied by this observation
    """
    canonical_key = part.get("canonical_part_key", "")
    if not canonical_key:
        return None

    existing = (
        db.query(PartMaster)
        .filter(PartMaster.canonical_part_key == canonical_key)
        .first()
    )

    incoming_specs = (
        part.get("specs", {}) if isinstance(part.get("specs", {}), dict) else {}
    )

    if existing:
        existing.observation_count = (existing.observation_count or 0) + 1
        existing.updated_at = datetime.utcnow()

        # Promote confidence only upward
        engine_conf = float(part.get("classification_confidence", 0) or 0)
        if engine_conf > float(existing.confidence or 0):
            existing.confidence = engine_conf

        # Fill blanks — never overwrite existing data
        if not existing.description and part.get("description"):
            existing.description = part.get("description", "")
        if not existing.mpn and part.get("mpn"):
            existing.mpn = part.get("mpn", "")
        if not existing.manufacturer and part.get("manufacturer"):
            existing.manufacturer = part.get("manufacturer", "")
        if not existing.material and part.get("material"):
            existing.material = part.get("material", "")
        if not existing.material_grade and incoming_specs.get("material_grade"):
            existing.material_grade = incoming_specs.get("material_grade", "")
        if not existing.material_form and part.get("material_form"):
            existing.material_form = part.get("material_form", "")

        # Merge specs additively
        existing.specs = _merge_specs(existing.specs or {}, incoming_specs)

        _create_aliases(db, existing, part)
        db.flush()
        return existing

    # ── Insert new canonical record ──
    category = _clean_text(part.get("category") or "unknown")
    pm = PartMaster(
        canonical_part_key=canonical_key,
        domain=category,
        category=category,
        procurement_class=part.get("procurement_class", "catalog_purchase"),
        description=part.get("description", ""),
        mpn=part.get("mpn", ""),
        manufacturer=part.get("manufacturer", ""),
        material=part.get("material", ""),
        material_grade=incoming_specs.get("material_grade", ""),
        material_form=part.get("material_form", ""),
        specs=incoming_specs,
        review_status=part.get("review_status", "auto"),
        confidence=part.get("classification_confidence", 0),
        source="observed",
        observation_count=1,
    )
    db.add(pm)
    db.flush()

    _create_aliases(db, pm, part)
    return pm


# ══════════════════════════════════════════════════════════════════════════════
# SPEC MERGE
# ══════════════════════════════════════════════════════════════════════════════

def _merge_specs(
    current: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Additive merge — never overwrites an existing value."""
    merged = dict(current or {})
    for k, v in (incoming or {}).items():
        if v in (None, "", [], {}):
            continue
        if k not in merged or merged.get(k) in (None, "", [], {}):
            merged[k] = v
    return merged


# ══════════════════════════════════════════════════════════════════════════════
# ALIAS MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _create_aliases(db: Session, pm: PartMaster, part: Dict[str, Any]):
    """
    Register known identifiers for a PartMaster as PartAlias rows.
    Idempotent — skips duplicates.
    """
    mpn = _clean_mpn(part.get("mpn", ""))
    if mpn and len(mpn) >= 3:
        _add_alias(db, pm.id, "mpn", part.get("mpn", ""), mpn.lower())

    desc_text = _clean_text(
        part.get("description") or part.get("standard_text") or ""
    )
    if desc_text and len(desc_text) >= 5:
        _add_alias(
            db,
            pm.id,
            "description",
            part.get("description") or desc_text,
            desc_text,
        )

    part_number = _clean_text(part.get("part_number") or "")
    if part_number and part_number != mpn and len(part_number) >= 3:
        _add_alias(
            db, pm.id, "supplier_pn", part.get("part_number"), part_number
        )

    mfr = _clean_text(part.get("manufacturer") or "")
    if mfr and len(mfr) >= 3:
        _add_alias(db, pm.id, "manufacturer", part.get("manufacturer"), mfr)


def _add_alias(
    db: Session,
    part_master_id: str,
    alias_type: str,
    alias_value: str,
    normalized_value: str,
):
    """Insert alias if not already present (deduplication by type + normalized_value)."""
    existing = (
    db.query(PartAlias)
    .filter(
        PartAlias.part_master_id == part_master_id,
        PartAlias.alias_type == alias_type,
        PartAlias.normalized_value == normalized_value,
    )
    .first()
)
    if not existing:
        db.add(
            PartAlias(
                part_master_id=part_master_id,
                alias_type=alias_type,
                alias_value=alias_value,
                normalized_value=normalized_value,
            )
        )
        db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# BATCH RESOLVE + LEARN  (main entry point)
# ══════════════════════════════════════════════════════════════════════════════

def resolve_and_learn(
    db: Session,
    bom_parts: List[Dict[str, Any]],
    bom_id: str,
    source_file: str = "",
) -> List[Dict[str, Any]]:
    """
    Resolve every part in a BOM and immediately learn from each decision.

    For each part:
      - auto_matched / review  → increment observation_count, merge specs,
                                 create aliases, record observation + attributes
      - unresolved (create_new) → upsert new PartMaster, record observation
                                  + attributes, surface matched_master_id
      - error                  → log warning, append error record, never abort
                                  the whole batch

    All metadata fields (bom_id, source_file, source_sheet, source_row,
    raw_text, normalized_text) are propagated into every decision dict
    so callers can persist or audit them directly.
    """
    results = []

    for part in bom_parts:
        try:
            part = dict(part or {})

            # Guard: skip entirely empty / None payloads — nothing to resolve
            if not part:
                results.append({
                    "match_status": "error",
                    "match_score": 0,
                    "match_reason": "Empty part payload",
                    "matched_master_id": None,
                    "canonical_part_key": "",
                    "candidates": [],
                    "action": "skip",
                    "bom_id": bom_id,
                })
                continue

            part.setdefault("bom_id", bom_id)
            part.setdefault("source_file", source_file)

            decision = resolve_part(db, part) or {}

            # Guarantee every downstream key exists — resolve_part should
            # always return a full dict but defensive defaults prevent
            # KeyError explosions in any code path that follows.
            decision.setdefault("match_status", "error")
            decision.setdefault("match_score", 0)
            decision.setdefault("match_reason", "Unknown")
            decision.setdefault("action", "skip")
            decision.setdefault("candidates", [])
            decision.setdefault("matched_master_id", None)
            decision.setdefault("canonical_part_key", part.get("canonical_part_key", ""))

            # ── Enrich decision with traceability fields ──
            decision["bom_part_id"] = part.get("bom_part_id")
            decision["bom_id"] = bom_id
            decision["source_file"] = part.get("source_file", source_file)
            decision["source_sheet"] = part.get("source_sheet", "")
            decision["source_row"] = part.get("source_row")
            decision["raw_text"] = part.get(
                "raw_text", part.get("description", "")
            )
            decision["normalized_text"] = part.get(
                "standard_text", part.get("normalized_text", "")
            )

            matched_master_id = decision.get("matched_master_id")

            # ── CREATE NEW canonical entry ──
            if decision["action"] == "create_new":
                pm = upsert_canonical_part(db, part)
                if pm:
                    decision["matched_master_id"] = pm.id
                    decision["match_reason"] = (
                        "New canonical entry created from observation"
                    )
                    _record_observation(
                        db, pm.id, part, decision,
                        bom_id=bom_id, source_file=source_file,
                    )
                    _extract_and_store_attributes(db, pm.id, part)

            # ── ACCEPT / REVIEW — reinforce existing candidate ──
            elif decision["action"] in ("accept", "review") and matched_master_id:
                pm = (
                    db.query(PartMaster)
                    .filter(PartMaster.id == matched_master_id)
                    .first()
                )
                if pm:
                    pm.observation_count = (pm.observation_count or 0) + 1
                    pm.updated_at = datetime.utcnow()

                    # Promote confidence only upward
                    engine_conf = float(
                        part.get("classification_confidence", 0) or 0
                    )
                    if engine_conf > float(pm.confidence or 0):
                        pm.confidence = engine_conf

                    # Merge specs additively
                    existing_specs = pm.specs or {}
                    incoming_specs = (
                        part.get("specs", {})
                        if isinstance(part.get("specs", {}), dict)
                        else {}
                    )
                    pm.specs = _merge_specs(existing_specs, incoming_specs)

                    # Expand alias index with this observation's identifiers
                    _create_aliases(db, pm, part)

                    _record_observation(
                        db, pm.id, part, decision,
                        bom_id=bom_id, source_file=source_file,
                    )
                    _extract_and_store_attributes(db, pm.id, part)

            results.append(decision)

        except Exception as e:
            logger.warning(
                f"Resolve failed for item_id={part.get('item_id', '?')}: {e}"
            )
            results.append(
                {
                    "match_status": "error",
                    "match_score": 0,
                    "match_reason": str(e),
                    "matched_master_id": None,
                    "canonical_part_key": part.get("canonical_part_key", ""),
                    "candidates": [],
                    "action": "skip",
                    "bom_part_id": part.get("bom_part_id"),
                    "bom_id": bom_id,
                }
            )

    return results


# ══════════════════════════════════════════════════════════════════════════════
# OBSERVATION RECORDING
# ══════════════════════════════════════════════════════════════════════════════

def _record_observation(
    db: Session,
    part_master_id: str,
    part: Dict[str, Any],
    decision: Dict[str, Any],
    bom_id: str,
    source_file: str = "",
):
    """
    Persist a PartObservation row for every resolve decision.
    All optional fields have explicit safe defaults so the insert never
    raises a NOT-NULL violation from a missing key.
    """
    try:
        db.add(
            PartObservation(
                part_master_id=part_master_id,
                bom_id=bom_id,
                bom_part_id=part.get("bom_part_id"),
                raw_text=part.get("raw_text", part.get("description", "")),
                normalized_text=part.get(
                    "standard_text", part.get("normalized_text", "")
                ),
                # Explicit cast: quantity may arrive as string "1" or None
                quantity=int(part.get("quantity") or 1),
                source_file=source_file or part.get("source_file", ""),
                source_sheet=part.get("source_sheet", ""),
                source_row=part.get("source_row"),
                # Explicit cast: match_score may be None if decision was empty
                match_score=float(decision.get("match_score") or 0),
                match_method=decision.get("match_status", "auto"),
            )
        )
    except Exception as e:
        logger.debug(f"[Resolver] Observation insert failed for part_master_id={part_master_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ATTRIBUTE EXTRACTION  (guarded by domain-specific whitelist)
# ══════════════════════════════════════════════════════════════════════════════

# Keys that are worth indexing as PartAttribute rows for downstream
# filtering, search, and analytics.  Anything outside this set stays
# in the JSON specs blob only — keeping the attribute table lean.
_INDEX_KEYS = {
    # Electrical / Electronics
    "resistance_ohm", "capacitance_f", "inductance_h", "voltage_v",
    "current_a", "package", "tolerance_pct", "frequency_hz", "power_w",
    "connector_type", "wire_gauge_awg", "shielding", "core_count",
    "insulation", "temperature_rating_c",

    # Mechanical / Fastener
    "thread_size", "length_mm", "diameter_mm", "thickness_mm",
    "material_grade", "material_name", "material_family", "fastener_type",
    "head_type", "coating", "standard", "form", "finish",

    # Pneumatic / Hydraulic
    "pressure_bar", "flow_rate_lpm", "port_size", "seal_type", "media",
    "valve_type", "cylinder_bore", "stroke_mm",

    # Optical
    "wavelength_nm", "fiber_type", "core_diameter_um", "lens_type",
    "optical_power_mw", "connector_polish",

    # Thermal
    "thermal_resistance_k_per_w", "thermal_conductivity_w_mk",
    "fan_size_mm", "airflow_cfm", "heatsink_type", "pad_thickness_mm",
}


def _extract_and_store_attributes(
    db: Session, part_master_id: str, part: Dict[str, Any]
):
    """
    Promote whitelisted spec keys to PartAttribute rows for indexed access.

    - Skips None / empty values.
    - Skips keys already stored (idempotent).
    - Attempts numeric parsing for range / filter queries.
    - Silently swallows per-attribute failures so one bad value
      does not block the rest.
    """
    specs = part.get("specs", {})
    if not specs or not isinstance(specs, dict):
        return

    for key in _INDEX_KEYS:
        try:
            val = specs.get(key)
            if val is None:
                continue

            str_val = str(val).strip()
            if not str_val or str_val == "None":
                continue

            # Idempotency check — do not duplicate attributes
            existing = (
                db.query(PartAttribute)
                .filter(
                    PartAttribute.part_master_id == part_master_id,
                    PartAttribute.attribute_key == key,
                )
                .first()
            )
            if existing:
                continue

            numeric: Optional[float] = None
            try:
                numeric = float(val)
            except (ValueError, TypeError):
                pass

            db.add(
                PartAttribute(
                    part_master_id=part_master_id,
                    attribute_key=key,
                    attribute_value=str_val[:255],
                    numeric_value=numeric,
                    source="extracted",
                    confidence=0.8,
                )
            )

        except Exception:
            # One bad attribute must never abort the rest of the loop
            continue


# ══════════════════════════════════════════════════════════════════════════════
# BOM PART LINKAGE  (write match decisions back to BOMPart rows)
# ══════════════════════════════════════════════════════════════════════════════

def update_bom_parts_with_matches(
    db: Session,
    bom_id: str,
    match_results: List[Dict[str, Any]],
    bom_parts_dicts: List[Dict[str, Any]],
):
    """
    Write resolved part_master_id, canonical_part_key, and review_status
    back onto every BOMPart row in the given BOM.

    Uses a single bulk fetch + dict lookup to avoid N+1 queries.
    """
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).all()
    part_by_id: Dict[str, BOMPart] = {str(p.id): p for p in parts}

    for mr in match_results:
        bom_part_id = str(mr.get("bom_part_id") or "")
        part = part_by_id.get(bom_part_id)
        if not part:
            continue

        # Link to resolved canonical master
        if mr.get("matched_master_id"):
            part.part_master_id = mr["matched_master_id"]

        # Always sync canonical_part_key — it must stay consistent even when
        # matched_master_id is not yet available (e.g. review_needed rows
        # whose master ID will be confirmed later by a human).
        if mr.get("canonical_part_key"):
            part.canonical_part_key = mr["canonical_part_key"]

        # Update review workflow status
        status = mr.get("match_status")
        if status == "review_needed":
            part.review_status = "needs_review"
        elif status == "auto_matched":
            part.review_status = "auto_matched"
        elif status == "unresolved":
            part.review_status = "unresolved"
        # "error" / "skip" — leave review_status unchanged

    db.flush()