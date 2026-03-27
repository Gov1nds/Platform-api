"""Auth routes — register, login, and /me.
Updated for PostgreSQL schema (auth.users, auth.guest_sessions).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas.user import UserRegister, UserLogin, TokenResponse, UserResponse
from app.models.user import User
from app.utils.dependencies import require_user
from sqlalchemy import text

router = APIRouter(prefix="/auth", tags=["auth"])


def attach_guest_boms(db, user_id: str, session_token: str):
    """Merge guest session data to authenticated user using the DB function."""
    if not session_token:
        return
    try:
        db.execute(
            text("SELECT auth.merge_guest_session(:token, :uid)"),
            {"token": session_token, "uid": user_id},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
            db.execute(
                text("""
                    UPDATE bom.boms SET uploaded_by_user_id = :uid, updated_at = now()
                    WHERE guest_session_id IN (
                        SELECT id FROM auth.guest_sessions WHERE session_token = :token
                    ) AND uploaded_by_user_id IS NULL
                """),
                {"uid": user_id, "token": session_token},
            )
            db.execute(
                text("""
                    UPDATE projects.projects SET user_id = :uid, updated_at = now()
                    WHERE guest_session_id IN (
                        SELECT id FROM auth.guest_sessions WHERE session_token = :token
                    ) AND user_id IS NULL
                """),
                {"uid": user_id, "token": session_token},
            )
            db.execute(
                text("""
                    UPDATE bom.analysis_results SET user_id = :uid, updated_at = now()
                    WHERE guest_session_id IN (
                        SELECT id FROM auth.guest_sessions WHERE session_token = :token
                    ) AND user_id IS NULL
                """),
                {"uid": user_id, "token": session_token},
            )
            db.commit()
        except Exception:
            db.rollback()


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    attach_guest_boms(db, user.id, body.session_token)
    token = create_access_token({"sub": user.id, "email": user.email})
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email, full_name=user.full_name),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    attach_guest_boms(db, user.id, body.session_token)
    token = create_access_token({"sub": user.id, "email": user.email})
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email, full_name=user.full_name),
    )


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(require_user)):
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
    )
