"""FastAPI dependencies — DB session, auth, and workflow access helpers."""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User
from app.models.project_access import ProjectParticipant, ProjectParticipantStatus, ProjectParticipantAccessLevel, ProjectParticipantType

security_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Returns user if authenticated, None otherwise."""
    if not credentials:
        return None
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def require_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def require_roles(*allowed_roles: str):
    allowed = {str(r).lower() for r in allowed_roles}

    def _dep(user: Optional[User] = Depends(get_current_user)) -> User:
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        role = str(getattr(user, "role", "")).lower()
        if allowed and role not in allowed and role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _dep


def is_collaboration_role(user: User) -> bool:
    return str(getattr(user, "role", "")).lower() in {"admin", "manager", "sourcing", "buyer", "vendor"}


def _as_id_set(values) -> set[str]:
    out: set[str] = set()
    if not values:
        return out
    if isinstance(values, (list, tuple, set)):
        for value in values:
            if value is None:
                continue
            out.add(str(value))
        return out
    out.add(str(values))
    return out


def _project_metadata(project) -> dict:
    meta = getattr(project, "project_metadata", None) or {}
    return meta if isinstance(meta, dict) else {}


def can_access_project(user: User, project, db: Optional[Session] = None) -> bool:
    if not user or not project:
        return False

    role = str(getattr(user, "role", "")).lower()
    if role == "admin":
        return True

    if getattr(project, "user_id", None) and str(project.user_id) == str(user.id):
        return True

    # Check project participants (ORM relationship or direct query)
    participants = list(getattr(project, "participants", []) or [])
    if not participants and db is not None and getattr(project, "id", None):
        participants = (
            db.query(ProjectParticipant)
            .filter(ProjectParticipant.project_id == project.id)
            .all()
        )
    for participant in participants:
        if getattr(participant, "user_id", None) and str(participant.user_id) == str(user.id):
            if str(getattr(participant, "status", "")).lower() in {"active", "invited", "pending"}:
                return True
        invited_email = str(getattr(participant, "invited_email", "")).strip().lower()
        if invited_email and invited_email == str(getattr(user, "email", "")).strip().lower():
            if str(getattr(participant, "status", "")).lower() in {"active", "invited", "pending"}:
                return True

    # Check project metadata collaborator lists
    meta = _project_metadata(project)
    collaborator_ids = _as_id_set(meta.get("collaborator_user_ids"))
    collaborator_emails = {str(v).lower() for v in _as_id_set(meta.get("collaborator_emails"))}
    if str(user.id) in collaborator_ids:
        return True
    if str(getattr(user, "email", "")).lower() in collaborator_emails:
        return True

    return False


def _participant_match(participant: ProjectParticipant, user: User) -> bool:
    if getattr(participant, "user_id", None) and str(participant.user_id) == str(user.id):
        return True
    invited_email = str(getattr(participant, "invited_email", "")).strip().lower()
    return bool(invited_email and invited_email == str(getattr(user, "email", "")).strip().lower())


def build_project_access_context(user: Optional[User], project, db: Optional[Session] = None) -> dict:
    if not project:
        return {
            "is_owner": False,
            "is_participant": False,
            "participant_type": None,
            "access_level": None,
            "can_view": False,
            "can_comment": False,
            "can_approve": False,
            "can_edit": False,
            "can_manage": False,
            "can_vendor_match": False,
            "can_chat": False,
            "can_order": False,
            "can_track": False,
        }

    role = str(getattr(user, "role", "")).lower() if user else ""
    owner_id = str(getattr(project, "user_id", "") or "")
    is_owner = bool(user and owner_id and str(user.id) == owner_id)
    participant = None

    participants = list(getattr(project, "participants", []) or [])
    if not participants and db is not None and getattr(project, "id", None):
        participants = (
            db.query(ProjectParticipant)
            .filter(ProjectParticipant.project_id == project.id)
            .all()
        )

    for row in participants:
        if user and _participant_match(row, user):
            participant = row
            break

    is_participant = participant is not None
    participant_type = str(getattr(participant, "participant_type", "")).lower() if participant else None
    access_level = str(getattr(participant, "access_level", "")).lower() if participant else None

    can_view = bool(user and (role == "admin" or is_owner or is_participant))
    if not user:
        can_view = False
    can_comment = can_view and (is_owner or is_participant or role in {"admin", "manager", "buyer", "sourcing", "vendor"})
    can_approve = can_view and (role == "admin" or role in {"manager", "buyer"} or access_level in {"approve", "edit", "manage"} or participant_type == "approver")
    can_edit = can_view and (role == "admin" or is_owner or access_level in {"edit", "manage"} or participant_type in {"collaborator", "owner"})
    can_manage = can_view and (role == "admin" or is_owner or access_level == "manage" or participant_type == "owner")
    can_vendor_match = bool(can_view)
    can_chat = bool(can_view)
    can_order = can_view and (role == "admin" or role in {"manager", "buyer", "sourcing"} or access_level in {"approve", "edit", "manage"})
    can_track = bool(can_view)

    return {
        "is_owner": is_owner,
        "is_participant": is_participant,
        "participant_type": participant_type,
        "access_level": access_level,
        "can_view": can_view,
        "can_comment": can_comment,
        "can_approve": can_approve,
        "can_edit": can_edit,
        "can_manage": can_manage,
        "can_vendor_match": can_vendor_match,
        "can_chat": can_chat,
        "can_order": can_order,
        "can_track": can_track,
    }
