"""
Review Service — explicit review queue for unresolved BOM items.

Handles:
  - Creating review queue items from resolver results
  - Assigning reviewers
  - Resolving items (match to existing, create new canonical, reject)
  - Promoting approved items into canonical master
  - Tracking resolution history
"""
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.catalog import ReviewQueueItem, PartMaster, PartAlias, PartObservation
from app.models.bom import BOMPart

logger = logging.getLogger("review_service")


def create_review_items_from_resolver(
    db: Session,
    bom_id: str,
    match_results: List[Dict[str, Any]],
    bom_parts_dicts: List[Dict[str, Any]],
):
    """Create ReviewQueueItem entries for all items that need review."""
    parts = db.query(BOMPart).filter(BOMPart.bom_id == bom_id).order_by(BOMPart.source_row).all()
    created = 0

    for i, part in enumerate(parts):
        if i >= len(match_results):
            break
        mr = match_results[i]
        if mr.get("match_status") not in ("review_needed", "unresolved"):
            continue

        # Check if already in queue
        existing = db.query(ReviewQueueItem).filter(
            ReviewQueueItem.bom_part_id == part.id,
            ReviewQueueItem.status.in_(("pending", "assigned")),
        ).first()
        if existing:
            continue

        db.add(ReviewQueueItem(
            bom_part_id=part.id,
            bom_id=bom_id,
            canonical_part_key=part.canonical_part_key,
            raw_text=part.raw_text,
            normalized_text=part.normalized_text,
            category=part.category_code,
            match_score=mr.get("match_score", 0),
            best_candidate_id=mr.get("matched_master_id"),
            candidates_json=mr.get("candidates", []),
            status="pending",
        ))
        created += 1

    if created:
        db.flush()
        logger.info(f"Created {created} review queue items for BOM {bom_id}")
    return created


def get_pending_reviews(db: Session, limit: int = 50, category: str = None) -> List[Dict]:
    """Get pending review items, optionally filtered by category."""
    query = db.query(ReviewQueueItem).filter(
        ReviewQueueItem.status.in_(("pending", "assigned"))
    )
    if category:
        query = query.filter(ReviewQueueItem.category == category)
    items = query.order_by(desc(ReviewQueueItem.created_at)).limit(limit).all()

    return [
        {
            "id": item.id,
            "bom_part_id": item.bom_part_id,
            "bom_id": item.bom_id,
            "canonical_part_key": item.canonical_part_key,
            "raw_text": item.raw_text,
            "normalized_text": item.normalized_text,
            "category": item.category,
            "match_score": float(item.match_score) if item.match_score else 0,
            "best_candidate_id": item.best_candidate_id,
            "candidates": item.candidates_json or [],
            "status": item.status,
            "assigned_to": item.assigned_to,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in items
    ]


def assign_review(db: Session, review_id: str, user_id: str) -> Optional[Dict]:
    """Assign a review item to a specific user."""
    item = db.query(ReviewQueueItem).filter(ReviewQueueItem.id == review_id).first()
    if not item:
        return None
    item.assigned_to = user_id
    item.status = "assigned"
    db.flush()
    return {"id": item.id, "status": "assigned", "assigned_to": user_id}


def resolve_as_match(db: Session, review_id: str, target_master_id: str,
                     user_id: str, comments: str = "") -> Optional[Dict]:
    """Resolve review item by matching to an existing canonical part."""
    item = db.query(ReviewQueueItem).filter(ReviewQueueItem.id == review_id).first()
    if not item:
        return None

    master = db.query(PartMaster).filter(PartMaster.id == target_master_id).first()
    if not master:
        return None

    item.status = "resolved"
    item.resolution = "matched"
    item.resolution_target_id = target_master_id
    item.reviewer_comments = comments
    item.resolved_at = datetime.utcnow()
    item.resolved_by = user_id

    # Update the BOM part
    bom_part = db.query(BOMPart).filter(BOMPart.id == item.bom_part_id).first()
    if bom_part:
        bom_part.part_master_id = target_master_id
        bom_part.review_status = "reviewed_matched"

    # Increment observation count on master
    master.observation_count = (master.observation_count or 0) + 1
    master.updated_at = datetime.utcnow()

    # Record observation
    db.add(PartObservation(
        part_master_id=target_master_id,
        bom_id=item.bom_id,
        bom_part_id=item.bom_part_id,
        raw_text=item.raw_text,
        normalized_text=item.normalized_text,
        match_score=item.match_score,
        match_method="human_review",
    ))

    db.flush()
    return {"id": item.id, "status": "resolved", "resolution": "matched", "target": target_master_id}


def resolve_as_new_canonical(db: Session, review_id: str, user_id: str,
                              comments: str = "", override_data: Dict = None) -> Optional[Dict]:
    """Promote an unresolved item into a new canonical master entry."""
    item = db.query(ReviewQueueItem).filter(ReviewQueueItem.id == review_id).first()
    if not item:
        return None

    bom_part = db.query(BOMPart).filter(BOMPart.id == item.bom_part_id).first()
    if not bom_part:
        return None

    override = override_data or {}
    canonical_key = override.get("canonical_part_key") or item.canonical_part_key or ""
    if not canonical_key:
        return None

    # Check for existing
    existing = db.query(PartMaster).filter(PartMaster.canonical_part_key == canonical_key).first()
    if existing:
        return resolve_as_match(db, review_id, existing.id, user_id, comments)

    # Create new canonical entry
    new_master = PartMaster(
        canonical_part_key=canonical_key,
        domain=override.get("domain") or (item.category or "unknown"),
        category=override.get("category") or item.category,
        procurement_class=override.get("procurement_class") or bom_part.procurement_class,
        description=override.get("description") or bom_part.description,
        mpn=override.get("mpn") or bom_part.mpn,
        manufacturer=override.get("manufacturer") or bom_part.manufacturer,
        material=override.get("material") or bom_part.material,
        material_grade=bom_part.specs.get("material_grade") if bom_part.specs else None,
        material_form=bom_part.material_form,
        specs=bom_part.specs or {},
        review_status="human_approved",
        confidence=0.95,
        source="human_review",
        observation_count=1,
    )
    db.add(new_master)
    db.flush()

    # Create aliases
    if bom_part.mpn and len(bom_part.mpn) >= 3:
        db.add(PartAlias(
            part_master_id=new_master.id,
            alias_type="mpn",
            alias_value=bom_part.mpn,
            normalized_value=bom_part.mpn.strip().upper(),
        ))
    if bom_part.description and len(bom_part.description) >= 5:
        db.add(PartAlias(
            part_master_id=new_master.id,
            alias_type="description",
            alias_value=bom_part.description,
            normalized_value=bom_part.description.strip().lower()[:40],
        ))

    # Update review item
    item.status = "promoted"
    item.resolution = "new_canonical"
    item.resolution_target_id = new_master.id
    item.reviewer_comments = comments
    item.resolved_at = datetime.utcnow()
    item.resolved_by = user_id

    # Update BOM part
    bom_part.part_master_id = new_master.id
    bom_part.review_status = "reviewed_promoted"

    # Record observation
    db.add(PartObservation(
        part_master_id=new_master.id,
        bom_id=item.bom_id,
        bom_part_id=item.bom_part_id,
        raw_text=item.raw_text,
        normalized_text=item.normalized_text,
        match_score=1.0,
        match_method="human_promoted",
    ))

    db.flush()
    return {"id": item.id, "status": "promoted", "new_master_id": new_master.id}


def resolve_as_rejected(db: Session, review_id: str, user_id: str,
                         comments: str = "") -> Optional[Dict]:
    """Reject a review item (e.g. junk data, invalid row)."""
    item = db.query(ReviewQueueItem).filter(ReviewQueueItem.id == review_id).first()
    if not item:
        return None
    item.status = "rejected"
    item.resolution = "rejected"
    item.reviewer_comments = comments
    item.resolved_at = datetime.utcnow()
    item.resolved_by = user_id

    bom_part = db.query(BOMPart).filter(BOMPart.id == item.bom_part_id).first()
    if bom_part:
        bom_part.review_status = "rejected"

    db.flush()
    return {"id": item.id, "status": "rejected"}


def get_review_stats(db: Session) -> Dict:
    """Get summary stats for the review queue."""
    pending = db.query(ReviewQueueItem).filter(ReviewQueueItem.status == "pending").count()
    assigned = db.query(ReviewQueueItem).filter(ReviewQueueItem.status == "assigned").count()
    resolved = db.query(ReviewQueueItem).filter(ReviewQueueItem.status == "resolved").count()
    promoted = db.query(ReviewQueueItem).filter(ReviewQueueItem.status == "promoted").count()
    rejected = db.query(ReviewQueueItem).filter(ReviewQueueItem.status == "rejected").count()
    return {
        "pending": pending,
        "assigned": assigned,
        "resolved": resolved,
        "promoted": promoted,
        "rejected": rejected,
        "total": pending + assigned + resolved + promoted + rejected,
    }
