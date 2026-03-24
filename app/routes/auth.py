"""Auth routes — register and login."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas.user import UserRegister, UserLogin, TokenResponse, UserResponse
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


# 🔥 HELPER FUNCTION (MOVE TO TOP FOR CLARITY)
def attach_guest_boms(db: Session, user_id: str, session_token: str | None):
    if not session_token:
        return

    db.execute(
        """
        UPDATE boms
        SET user_id = :user_id
        WHERE session_token = :session_token
        AND user_id IS NULL
        """,
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

    # 🔥 LINK GUEST BOMS
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

    # 🔥 LINK GUEST BOMS
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