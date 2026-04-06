from fastapi import Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User, VendorUser
from app.models.project import Project, ProjectACL


def get_current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User | None:
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
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def get_vendor_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> VendorUser | None:
    if not authorization.startswith("Bearer "):
        return None
    payload = decode_token(authorization.removeprefix("Bearer ").strip())
    if not payload or payload.get("type") != "vendor":
        return None
    return db.query(VendorUser).filter(VendorUser.id == payload["sub"]).first()


def require_vendor_user(vu: VendorUser | None = Depends(get_vendor_user)) -> VendorUser:
    if not vu:
        raise HTTPException(401, "Vendor authentication required")
    return vu


# ── Object-scoped access guards ──────────────────

def _check_project_access(db: Session, project: Project, user: User | None,
                          session_token: str | None, require_role: str | None = None) -> bool:
    """ACL-based project access. Returns True if allowed, raises 403 otherwise."""
    if not project:
        raise HTTPException(404, "Project not found")
    # Owner always allowed
    if user and project.user_id and project.user_id == user.id:
        return True
    # Admin always allowed
    if user and user.role == "admin":
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
    # Guest preview: only if project has guest_session_id AND the session_token matches
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


def require_project_access(project_id: str, db: Session = Depends(get_db),
                           user: User | None = Depends(get_current_user),
                           session_token: str = Query(default="")) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    _check_project_access(db, project, user, session_token)
    return project


def require_project_owner(project_id: str, db: Session = Depends(get_db),
                          user: User = Depends(require_user)) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    if project.user_id != user.id and user.role != "admin":
        raise HTTPException(403, "Not the project owner")
    return project
