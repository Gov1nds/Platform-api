"""
Auth routes — register, login, and /me.
Updated for PostgreSQL schema (auth.users, auth.guest_sessions).
"""
import token

import token

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas.user import UserRegister, UserLogin, TokenResponse, UserResponse
from app.models.user import User
from app.utils.dependencies import require_user
from app.services.auth_service import merge_guest_session


router = APIRouter(prefix="/auth", tags=["auth"])


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

    merge_result = merge_guest_session(db, user.id, body.session_token)

    # In register() after merge, before return:
    merged_project_id = None
    if merge_result.get("merged") and merge_result.get("project_ids"):
        merged_project_id = merge_result["project_ids"][0]

    return TokenResponse(
        access_token= token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
        ),
        # Add to TokenResponse schema:
        # merged_project_id: Optional[str] = None
    )
    token = create_access_token({"sub": user.id, "email": user.email})

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
        ),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    merge_result = merge_guest_session(db, user.id, body.session_token)

    token = create_access_token({"sub": user.id, "email": user.email})

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
        ),
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