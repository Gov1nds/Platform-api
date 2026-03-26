"""Auth routes — register, login, and /me endpoint."""

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
    """
    FIXED: Updates BOMs, Projects, AND AnalysisResults atomically.
    Previously only updated boms table, leaving orphaned projects.
    """
    if not session_token:
        return

    # Update BOMs
    db.execute(
        text("""
            UPDATE boms
            SET user_id = :user_id
            WHERE session_token = :session_token
            AND user_id IS NULL
        """),
        {"user_id": user_id, "session_token": session_token},
    )

    # FIXED: Also update projects that belong to those BOMs
    db.execute(
        text("""
            UPDATE projects
            SET user_id = :user_id
            WHERE bom_id IN (
                SELECT id FROM boms WHERE session_token = :session_token
            )
            AND user_id IS NULL
        """),
        {"user_id": user_id, "session_token": session_token},
    )

    # FIXED: Also update analysis results
    db.execute(
        text("""
            UPDATE analysis_results
            SET user_id = :user_id
            WHERE bom_id IN (
                SELECT id FROM boms WHERE session_token = :session_token
            )
            AND user_id IS NULL
        """),
        {"user_id": user_id, "session_token": session_token},
    )

    db.commit()


# =========================
# REGISTER
# =========================
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

    # Link guest BOMs + projects + analysis
    attach_guest_boms(db, user.id, body.session_token)

    token = create_access_token({"sub": user.id, "email": user.email})

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name
        ),
    )


# =========================
# LOGIN
# =========================
@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Link guest BOMs + projects + analysis
    attach_guest_boms(db, user.id, body.session_token)

    token = create_access_token({"sub": user.id, "email": user.email})

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name
        ),
    )


# =========================
# ME — NEW ENDPOINT
# =========================
@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(require_user)):
    """Return current authenticated user. Used for frontend auth hydration."""
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
    )