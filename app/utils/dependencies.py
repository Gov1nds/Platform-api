"""
FastAPI dependency injection functions for authentication, authorization,
role enforcement, organization scoping, and BOLA checks.

References: GAP-005 (role hierarchy, BOLA, org scoping),
            GAP-024 (auth guards), architecture.md CC-01, CC-07,
            roles-permissions.yaml ACL-001, ACL-002
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.enums import (
    BUYER_ROLE_HIERARCHY,
    VENDOR_ROLE_HIERARCHY,
    ActorType,
    BuyerRole,
    VendorRole,
)
from app.models.user import User, VendorUser
from app.models.project import Project, ProjectACL


# ── Actor dataclass ──────────────────────────────────────────────────────────

@dataclass
class Actor:
    """Unified representation of the authenticated caller."""
    id: str
    type: Literal["user", "vendor", "guest", "system"]
    role: str
    organization_id: str | None = None
    vendor_id: str | None = None
    email: str = ""


# ── Token extraction ─────────────────────────────────────────────────────────

def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User | None:
    if not authorization.startswith("Bearer "):
        return None
    payload = decode_token(authorization.removeprefix("Bearer ").strip())
    if not payload or "sub" not in payload or payload.get("type") == "vendor":
        return None
    return db.query(User).filter(User.id == payload["sub"]).first()


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role not in ("admin", BuyerRole.BUYER_ADMIN, BuyerRole.ORGANIZATION_OWNER):
        raise HTTPException(403, "Admin access required")
    return user


# ── Vendor auth ──────────────────────────────────────────────────────────────

def get_vendor_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> VendorUser | None:
    if not authorization.startswith("Bearer "):
        return None
    payload = decode_token(authorization.removeprefix("Bearer ").strip())
    if not payload or payload.get("type") != "vendor":
        return None
    return db.query(VendorUser).filter(VendorUser.id == payload["sub"]).first()


def require_vendor_user(
    vu: VendorUser | None = Depends(get_vendor_user),
) -> VendorUser:
    if not vu:
        raise HTTPException(401, "Vendor authentication required")
    return vu


# ── Organization scoping ────────────────────────────────────────────────────

def get_organization_id(request: Request) -> str | None:
    """Extract organization_id from request state (set by TenantIsolationMiddleware)."""
    return getattr(request.state, "organization_id", None)


def require_organization(request: Request) -> str:
    """Raise 403 if the caller has no organization context."""
    org_id = get_organization_id(request)
    if not org_id:
        raise HTTPException(403, "Organization context required")
    return org_id


# ── Role hierarchy enforcement (ACL-001) ─────────────────────────────────────

def require_role(minimum_role: str):
    """
    Dependency factory: ensures the caller's buyer role is at or above
    ``minimum_role`` in the hierarchy.

    Usage::

        @router.post("/…")
        def create_rfq(user: User = require_role("BUYER_EDITOR")):
            ...
    """

    def _dependency(
        user: User = Depends(require_user),
        request: Request = None,  # type: ignore[assignment]
    ) -> User:
        user_role = (user.role or "").upper()

        # Normalize legacy "buyer" / "admin" roles
        role_map = {
            "BUYER": BuyerRole.BUYER_EDITOR,
            "ADMIN": BuyerRole.BUYER_ADMIN,
        }
        effective_role = role_map.get(user_role, user_role)

        if effective_role not in BUYER_ROLE_HIERARCHY:
            raise HTTPException(403, f"Unknown role: {user.role}")

        user_level = BUYER_ROLE_HIERARCHY.index(effective_role)
        min_level = BUYER_ROLE_HIERARCHY.index(minimum_role.upper())

        if user_level < min_level:
            raise HTTPException(
                403,
                f"Requires {minimum_role} role or higher",
            )
        return user

    return Depends(_dependency)


def require_vendor_role(minimum_role: str):
    """Dependency factory for vendor role hierarchy."""

    def _dependency(
        vu: VendorUser = Depends(require_vendor_user),
    ) -> VendorUser:
        vu_role = (getattr(vu, "role", None) or VendorRole.VENDOR_REP).upper()

        if vu_role not in VENDOR_ROLE_HIERARCHY:
            raise HTTPException(403, f"Unknown vendor role: {vu_role}")

        vu_level = VENDOR_ROLE_HIERARCHY.index(vu_role)
        min_level = VENDOR_ROLE_HIERARCHY.index(minimum_role.upper())

        if vu_level < min_level:
            raise HTTPException(
                403,
                f"Requires {minimum_role} vendor role or higher",
            )
        return vu

    return Depends(_dependency)


# ── Organization-scoped project access (BOLA) ───────────────────────────────

def require_org_scoped_project(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Project:
    """
    Load a project by ID, enforcing that it belongs to the caller's
    organization. Prevents BOLA / cross-tenant access.
    """
    org_id = getattr(request.state, "organization_id", None)
    query = db.query(Project).filter(Project.id == project_id)

    # If the caller has an org context, scope to it
    if org_id and hasattr(Project, "organization_id"):
        query = query.filter(Project.organization_id == org_id)

    project = query.first()
    if not project:
        raise HTTPException(404, "Project not found")
    return project


# ── Object-scoped access guards (preserved + enhanced) ──────────────────────

def _check_project_access(
    db: Session,
    project: Project,
    user: User | None,
    session_token: str | None,
    require_role: str | None = None,
) -> bool:
    """ACL-based project access. Returns True if allowed, raises 403 otherwise."""
    if not project:
        raise HTTPException(404, "Project not found")

    # Owner always allowed
    if user and project.user_id and project.user_id == user.id:
        return True

    # Admin always allowed
    if user and user.role in ("admin", BuyerRole.BUYER_ADMIN, BuyerRole.ORGANIZATION_OWNER):
        return True

    # Check explicit ACL entries
    if user:
        acl = db.query(ProjectACL).filter(
            ProjectACL.project_id == project.id,
            ProjectACL.principal_type == "user",
            ProjectACL.principal_id == user.id,
        ).first()
        if acl:
            if require_role and acl.role not in (require_role, "owner", "editor"):
                raise HTTPException(403, "Insufficient role")
            return True

    # Guest preview
    if session_token and project.guest_session_id and project.visibility == "guest_preview":
        from app.models.user import GuestSession

        gs = db.query(GuestSession).filter(
            GuestSession.id == project.guest_session_id,
            GuestSession.session_token == session_token,
        ).first()
        if gs:
            if require_role and require_role not in ("viewer",):
                raise HTTPException(403, "Guest sessions have read-only access")
            return True

    raise HTTPException(403, "Access denied")


def require_project_access(
    project_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
    session_token: str = Query(default=""),
) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    _check_project_access(db, project, user, session_token)
    return project


def require_project_owner(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    if project.user_id != user.id and user.role not in (
        "admin", BuyerRole.BUYER_ADMIN, BuyerRole.ORGANIZATION_OWNER
    ):
        raise HTTPException(403, "Not the project owner")
    return project


def require_buyer_approver(
    user: User = Depends(require_user),
) -> User:
    """Dependency for approval workflows — requires buyer_approver or above."""
    role = (user.role or "").upper()
    role_map = {"ADMIN": BuyerRole.BUYER_ADMIN}
    effective = role_map.get(role, role)
    if effective not in BUYER_ROLE_HIERARCHY:
        raise HTTPException(403, f"Unknown role: {user.role}")
    level = BUYER_ROLE_HIERARCHY.index(effective)
    min_level = BUYER_ROLE_HIERARCHY.index(BuyerRole.BUYER_APPROVER)
    if level < min_level:
        raise HTTPException(403, "Requires buyer_approver role or higher")
    return user


# ── Unified actor resolution ────────────────────────────────────────────────

def get_current_actor(
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> Actor:
    """
    Resolve the current caller into a typed Actor regardless of whether
    they are a buyer user, vendor user, or guest.
    """
    if not authorization.startswith("Bearer "):
        # Guest / unauthenticated
        return Actor(
            id="anonymous",
            type="guest",
            role=BuyerRole.GUEST,
            organization_id=getattr(request.state, "organization_id", None),
        )

    payload = decode_token(authorization.removeprefix("Bearer ").strip())
    if not payload or "sub" not in payload:
        return Actor(id="anonymous", type="guest", role=BuyerRole.GUEST)

    if payload.get("type") == "vendor":
        vu = db.query(VendorUser).filter(VendorUser.id == payload["sub"]).first()
        if vu:
            return Actor(
                id=vu.id,
                type="vendor",
                role=getattr(vu, "role", VendorRole.VENDOR_REP) or VendorRole.VENDOR_REP,
                vendor_id=vu.vendor_id,
                email=vu.email,
            )

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if user:
        return Actor(
            id=user.id,
            type="user",
            role=user.role or BuyerRole.BUYER_EDITOR,
            organization_id=getattr(request.state, "organization_id", None),
            email=user.email,
        )

    return Actor(id=payload["sub"], type="user", role=BuyerRole.GUEST)

# ── Guest session dependency (Batch 3: GAP-001) ────────────────────────────

def get_guest_session_dep(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    FastAPI dependency: retrieve guest session from HttpOnly cookie.
    Returns GuestSession or None.
    """
    from app.services.guest_service import get_guest_session
    return get_guest_session(request, db)


def require_guest_or_user(
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """
    Dependency that allows either authenticated user or guest session.
    Returns (User | None, GuestSession | None). At least one must be present.
    """
    user = get_current_user(authorization=authorization, db=db)
    if user:
        return user, None

    from app.services.guest_service import get_guest_session
    gs = get_guest_session(request, db)
    if gs:
        return None, gs

    raise HTTPException(401, "Authentication or guest session required")

# ── Task 31: RBAC permission enforcement (Blueprint §31.1, C4) ────────────

class Permission:
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    RFQ_CREATE = "rfq:create"
    PO_APPROVE = "po:approve"
    VENDOR_INVITE = "vendor:invite"
    REPORT_EXPORT = "report:export"
    ORG_ADMIN = "org:admin"

ROLE_PERMISSIONS = {
    "owner":    {Permission.PROJECT_READ, Permission.PROJECT_WRITE, Permission.RFQ_CREATE,
                 Permission.PO_APPROVE, Permission.VENDOR_INVITE, Permission.REPORT_EXPORT,
                 Permission.ORG_ADMIN},
    "admin":    {Permission.PROJECT_WRITE, Permission.RFQ_CREATE, Permission.PO_APPROVE,
                 Permission.VENDOR_INVITE, Permission.REPORT_EXPORT},
    "approver": {Permission.PROJECT_READ, Permission.PO_APPROVE},
    "buyer":    {Permission.PROJECT_WRITE, Permission.RFQ_CREATE, Permission.VENDOR_INVITE},
    "viewer":   {Permission.PROJECT_READ, Permission.REPORT_EXPORT},
    "ORGANIZATION_OWNER": {Permission.PROJECT_READ, Permission.PROJECT_WRITE, Permission.RFQ_CREATE,
                           Permission.PO_APPROVE, Permission.VENDOR_INVITE, Permission.REPORT_EXPORT,
                           Permission.ORG_ADMIN},
    "BUYER_ADMIN": {Permission.PROJECT_WRITE, Permission.RFQ_CREATE, Permission.PO_APPROVE,
                    Permission.VENDOR_INVITE, Permission.REPORT_EXPORT},
    "BUYER_APPROVER": {Permission.PROJECT_READ, Permission.PO_APPROVE},
    "BUYER_EDITOR": {Permission.PROJECT_WRITE, Permission.RFQ_CREATE, Permission.VENDOR_INVITE},
    "BUYER_VIEWER": {Permission.PROJECT_READ, Permission.REPORT_EXPORT},
}

def require_permission(perm: str):
    """Dependency factory for fine-grained permission checks."""
    def _check(user=Depends(require_user), db=Depends(get_db)):
        role = (user.role or "viewer").upper()
        role_map = {"BUYER": "BUYER_EDITOR", "ADMIN": "BUYER_ADMIN"}
        effective = role_map.get(role, role)
        perms = ROLE_PERMISSIONS.get(effective, set()) | ROLE_PERMISSIONS.get(role.lower(), set())
        if perm not in perms:
            raise HTTPException(403, f"Permission denied: requires \'{perm}\' (role=\'{role}\')")
        return user
    return Depends(_check)
