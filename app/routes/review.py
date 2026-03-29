"""Review queue routes — operator API for unresolved BOM item review.

Endpoints:
  GET  /review/pending          — list pending review items
  GET  /review/stats            — queue summary stats
  POST /review/{id}/assign      — assign to reviewer
  POST /review/{id}/match       — resolve by matching to existing canonical
  POST /review/{id}/promote     — promote to new canonical entry
  POST /review/{id}/reject      — reject (junk/invalid)
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.utils.dependencies import require_user
from app.services import review_service

logger = logging.getLogger("routes.review")
router = APIRouter(prefix="/review", tags=["review"])


class AssignRequest(BaseModel):
    user_id: Optional[str] = None  # If None, assign to current user


class MatchRequest(BaseModel):
    target_master_id: str
    comments: str = ""


class PromoteRequest(BaseModel):
    canonical_part_key: Optional[str] = None
    category: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    mpn: Optional[str] = None
    manufacturer: Optional[str] = None
    material: Optional[str] = None
    procurement_class: Optional[str] = None
    comments: str = ""


class RejectRequest(BaseModel):
    comments: str = ""


@router.get("/pending")
def list_pending(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """List pending review items, optionally filtered by category."""
    return review_service.get_pending_reviews(db, limit=limit, category=category)


@router.get("/stats")
def review_stats(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get review queue summary: pending/assigned/resolved/promoted/rejected counts."""
    return review_service.get_review_stats(db)


@router.post("/{review_id}/assign")
def assign_review(
    review_id: str,
    body: AssignRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Assign a review item to a specific user (or current user if not specified)."""
    target_user_id = body.user_id or user.id
    result = review_service.assign_review(db, review_id, target_user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Review item not found")
    db.commit()
    return result


@router.post("/{review_id}/match")
def resolve_match(
    review_id: str,
    body: MatchRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Resolve a review item by matching it to an existing canonical part."""
    result = review_service.resolve_as_match(
        db, review_id, body.target_master_id, user.id, body.comments
    )
    if not result:
        raise HTTPException(status_code=404, detail="Review item or target master not found")
    db.commit()
    return result


@router.post("/{review_id}/promote")
def promote_to_canonical(
    review_id: str,
    body: PromoteRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Promote an unresolved item into a new canonical master entry."""
    override_data = {}
    if body.canonical_part_key:
        override_data["canonical_part_key"] = body.canonical_part_key
    if body.category:
        override_data["category"] = body.category
    if body.domain:
        override_data["domain"] = body.domain
    if body.description:
        override_data["description"] = body.description
    if body.mpn:
        override_data["mpn"] = body.mpn
    if body.manufacturer:
        override_data["manufacturer"] = body.manufacturer
    if body.material:
        override_data["material"] = body.material
    if body.procurement_class:
        override_data["procurement_class"] = body.procurement_class

    result = review_service.resolve_as_new_canonical(
        db, review_id, user.id, body.comments, override_data or None
    )
    if not result:
        raise HTTPException(status_code=404, detail="Review item not found or missing canonical key")
    db.commit()
    return result


@router.post("/{review_id}/reject")
def reject_review(
    review_id: str,
    body: RejectRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Reject a review item (junk data, invalid row, etc.)."""
    result = review_service.resolve_as_rejected(db, review_id, user.id, body.comments)
    if not result:
        raise HTTPException(status_code=404, detail="Review item not found")
    db.commit()
    return result
