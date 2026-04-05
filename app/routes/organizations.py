"""Organization, workspace, and membership routes.

P-3/DB-1/UX-2: Provides org tenancy so procurement teams can share
projects, approvals, analytics, and vendor intelligence within a workspace.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.organization import (
    Organization, Workspace, WorkspaceMembership,
    OrgStatus, MemberRole, MemberStatus,
)
from app.schemas.organization import (
    OrganizationCreate, OrganizationUpdate, OrganizationSchema,
    WorkspaceCreate, WorkspaceSchema,
    MemberInvite, MemberUpdate, MemberSchema,
)
from app.utils.dependencies import require_user

logger = logging.getLogger("routes.organizations")
router = APIRouter(prefix="/organizations", tags=["organizations"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_org_member(db: Session, org_id: str, user: User, min_role: str = "viewer"):
    """Verify user belongs to the org (via any workspace). Returns org."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    role = str(getattr(user, "role", "")).lower()
    if role == "admin":
        return org

    if org.owner_user_id and str(org.owner_user_id) == str(user.id):
        return org

    membership = (
        db.query(WorkspaceMembership)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(
            Workspace.organization_id == org_id,
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.status == MemberStatus.active.value,
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    return org


def _require_ws_member(db: Session, workspace_id: str, user: User, min_role: str = "viewer"):
    """Verify user is a member of the workspace. Returns (workspace, membership)."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    role = str(getattr(user, "role", "")).lower()
    if role == "admin":
        return ws, None

    org = db.query(Organization).filter(Organization.id == ws.organization_id).first()
    if org and org.owner_user_id and str(org.owner_user_id) == str(user.id):
        return ws, None

    membership = (
        db.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.status == MemberStatus.active.value,
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    ROLE_RANK = {"owner": 0, "admin": 1, "manager": 2, "buyer": 3, "sourcing": 4, "approver": 5, "viewer": 6, "vendor": 7}
    if ROLE_RANK.get(membership.role, 99) > ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return ws, membership


# ── Organizations ────────────────────────────────────────────────────────────

@router.post("", response_model=OrganizationSchema, status_code=201)
def create_org(
    body: OrganizationCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    existing = db.query(Organization).filter(Organization.slug == body.slug).first()
    if existing:
        raise HTTPException(status_code=400, detail="Organization slug already taken")

    org = Organization(
        name=body.name,
        slug=body.slug,
        owner_user_id=user.id,
        default_currency=body.default_currency,
        default_region=body.default_region,
    )
    db.add(org)
    db.flush()

    # Auto-create a default workspace
    ws = Workspace(
        organization_id=org.id,
        name="Default",
        description="Default workspace",
    )
    db.add(ws)
    db.flush()

    # Add owner as member
    membership = WorkspaceMembership(
        workspace_id=ws.id,
        user_id=user.id,
        role=MemberRole.owner.value,
        status=MemberStatus.active.value,
        joined_at=datetime.utcnow(),
    )
    db.add(membership)
    db.commit()
    db.refresh(org)

    return OrganizationSchema.model_validate(org)


@router.get("", response_model=List[OrganizationSchema])
def list_orgs(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    role = str(getattr(user, "role", "")).lower()
    if role == "admin":
        orgs = db.query(Organization).filter(Organization.status != OrgStatus.archived.value).all()
    else:
        org_ids = (
            db.query(Workspace.organization_id)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .filter(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.status == MemberStatus.active.value,
            )
            .distinct()
            .all()
        )
        ids = [r[0] for r in org_ids]
        owned = db.query(Organization.id).filter(Organization.owner_user_id == user.id).all()
        ids.extend([r[0] for r in owned])
        ids = list(set(ids))
        orgs = db.query(Organization).filter(Organization.id.in_(ids)).all() if ids else []

    return [OrganizationSchema.model_validate(o) for o in orgs]


@router.get("/{org_id}", response_model=OrganizationSchema)
def get_org(
    org_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    org = _require_org_member(db, org_id, user)
    return OrganizationSchema.model_validate(org)


@router.patch("/{org_id}", response_model=OrganizationSchema)
def update_org(
    org_id: str,
    body: OrganizationUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    org = _require_org_member(db, org_id, user, min_role="admin")
    if body.name is not None:
        org.name = body.name
    if body.default_currency is not None:
        org.default_currency = body.default_currency
    if body.default_region is not None:
        org.default_region = body.default_region
    if body.logo_url is not None:
        org.logo_url = body.logo_url
    db.commit()
    db.refresh(org)
    return OrganizationSchema.model_validate(org)


# ── Workspaces ───────────────────────────────────────────────────────────────

@router.post("/{org_id}/workspaces", response_model=WorkspaceSchema, status_code=201)
def create_workspace(
    org_id: str,
    body: WorkspaceCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_org_member(db, org_id, user, min_role="admin")

    ws = Workspace(
        organization_id=org_id,
        name=body.name,
        description=body.description,
        default_currency=body.default_currency,
        default_region=body.default_region,
        budget_limit=body.budget_limit,
    )
    db.add(ws)
    db.flush()

    # Add creator as member
    membership = WorkspaceMembership(
        workspace_id=ws.id,
        user_id=user.id,
        role=MemberRole.admin.value,
        status=MemberStatus.active.value,
        joined_at=datetime.utcnow(),
    )
    db.add(membership)
    db.commit()
    db.refresh(ws)

    result = WorkspaceSchema.model_validate(ws)
    result.member_count = 1
    return result


@router.get("/{org_id}/workspaces", response_model=List[WorkspaceSchema])
def list_workspaces(
    org_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_org_member(db, org_id, user)

    workspaces = (
        db.query(Workspace)
        .filter(Workspace.organization_id == org_id, Workspace.status != OrgStatus.archived.value)
        .all()
    )
    results = []
    for ws in workspaces:
        schema = WorkspaceSchema.model_validate(ws)
        schema.member_count = (
            db.query(WorkspaceMembership)
            .filter(WorkspaceMembership.workspace_id == ws.id, WorkspaceMembership.status == MemberStatus.active.value)
            .count()
        )
        results.append(schema)
    return results


# ── Members ──────────────────────────────────────────────────────────────────

@router.post("/{org_id}/workspaces/{workspace_id}/members", response_model=MemberSchema, status_code=201)
def invite_member(
    org_id: str,
    workspace_id: str,
    body: MemberInvite,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_org_member(db, org_id, user, min_role="admin")
    ws, _ = _require_ws_member(db, workspace_id, user, min_role="admin")

    # Find user by email
    target_user = db.query(User).filter(User.email == body.email).first()

    # Check if already a member
    if target_user:
        existing = (
            db.query(WorkspaceMembership)
            .filter(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == target_user.id,
            )
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="User is already a member")

    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=target_user.id if target_user else str(uuid.uuid4()),
        role=body.role,
        status=MemberStatus.invited.value if not target_user else MemberStatus.active.value,
        invited_email=body.email,
        invited_by_user_id=user.id,
        joined_at=datetime.utcnow() if target_user else None,
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)

    result = MemberSchema.model_validate(membership)
    if target_user:
        result.user_name = target_user.full_name
        result.user_email = target_user.email
    else:
        result.user_email = body.email
    return result


@router.get("/{org_id}/workspaces/{workspace_id}/members", response_model=List[MemberSchema])
def list_members(
    org_id: str,
    workspace_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_org_member(db, org_id, user)
    ws, _ = _require_ws_member(db, workspace_id, user)

    memberships = (
        db.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace_id)
        .all()
    )
    results = []
    for m in memberships:
        schema = MemberSchema.model_validate(m)
        member_user = db.query(User).filter(User.id == m.user_id).first()
        if member_user:
            schema.user_name = member_user.full_name
            schema.user_email = member_user.email
        results.append(schema)
    return results


@router.patch("/{org_id}/workspaces/{workspace_id}/members/{member_id}", response_model=MemberSchema)
def update_member(
    org_id: str,
    workspace_id: str,
    member_id: str,
    body: MemberUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_org_member(db, org_id, user, min_role="admin")
    ws, _ = _require_ws_member(db, workspace_id, user, min_role="admin")

    membership = db.query(WorkspaceMembership).filter(WorkspaceMembership.id == member_id).first()
    if not membership or str(membership.workspace_id) != workspace_id:
        raise HTTPException(status_code=404, detail="Member not found")

    if body.role is not None:
        membership.role = body.role
    if body.status is not None:
        membership.status = body.status
    db.commit()
    db.refresh(membership)

    result = MemberSchema.model_validate(membership)
    member_user = db.query(User).filter(User.id == membership.user_id).first()
    if member_user:
        result.user_name = member_user.full_name
        result.user_email = member_user.email
    return result
