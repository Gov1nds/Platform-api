"""FastAPI dependencies — DB session, auth, and workflow access helpers."""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User

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


def can_access_project(user: User, project) -> bool:
    if not user or not project:
        return False
    role = str(getattr(user, "role", "")).lower()
    if role == "admin":
        return True
    return bool(getattr(project, "user_id", None) and project.user_id == user.id)