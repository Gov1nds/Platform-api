"""
Auth routes — register, login, and /me.
Updated for PostgreSQL schema (auth.users, auth.guest_sessions).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas.user import UserRegister, UserLogin, TokenResponse, UserResponse, GuestMergeResult
from app.models.user import User
from app.utils.dependencies import require_user


router = APIRouter(prefix="/auth", tags=["auth"])


def _guest_session_row(db: Session, session_token: str):
    if not session_token:
        return None
    return db.execute(
        text("""
            SELECT id, session_token, merged_user_id, merged_at
            FROM auth.guest_sessions
            WHERE session_token = :token
            LIMIT 1
        """),
        {"token": session_token},
    ).mappings().first()


def _update_if_exists(db: Session, sql: str, params: dict) -> int:
    result = db.execute(text(sql), params)
    return int(getattr(result, "rowcount", 0) or 0)


def attach_guest_boms(db: Session, user_id: str, session_token: str):
    """
    Merge guest session data to an authenticated user in one transaction.
    Returns a summary dict and raises on any unexpected merge failure.
    """

    if not session_token:
        return {"status": "skipped", "reason": "missing_session_token", "merged": False}

    guest = _guest_session_row(db, session_token)
    if not guest:
        # No guest session means nothing to merge, but do not fail sign-up/login.
        return {"status": "skipped", "reason": "guest_session_not_found", "merged": False}

    guest_session_id = str(guest["id"])
    merge_summary = {
        "status": "merged",
        "merged": True,
        "guest_session_id": guest_session_id,
        "session_token": session_token,
        "boms": 0,
        "projects": 0,
        "analysis_results": 0,
        "rfqs": 0,
        "intake_sessions": 0,
        "drawing_assets": 0,
        "guest_sessions": 0,
    }

    try:
        # Transaction is managed by the calling endpoint (register/login).
        # No defensive rollback here — it would discard uncommitted caller work.

        merge_summary["boms"] = _update_if_exists(
            db,
            """
            UPDATE bom.boms
            SET uploaded_by_user_id = :uid,
                updated_at = now()
            WHERE guest_session_id = :guest_session_id
              AND (uploaded_by_user_id IS NULL OR uploaded_by_user_id <> :uid)
            """,
            {"uid": user_id, "guest_session_id": guest_session_id},
        )

        merge_summary["projects"] = _update_if_exists(
            db,
            """
            UPDATE projects.projects
            SET user_id = :uid,
                visibility_level = 'full',
                visibility = 'full',
                updated_at = now()
            WHERE guest_session_id = :guest_session_id
              AND (user_id IS NULL OR user_id <> :uid OR visibility_level <> 'full' OR visibility <> 'full')
            """,
            {"uid": user_id, "guest_session_id": guest_session_id},
        )

        merge_summary["analysis_results"] = _update_if_exists(
            db,
            """
            UPDATE bom.analysis_results
            SET user_id = :uid,
                updated_at = now()
            WHERE guest_session_id = :guest_session_id
              AND (user_id IS NULL OR user_id <> :uid)
            """,
            {"uid": user_id, "guest_session_id": guest_session_id},
        )

        merge_summary["rfqs"] = _update_if_exists(
            db,
            """
            UPDATE sourcing.rfq_batches
            SET requested_by_user_id = :uid,
                updated_at = now()
            WHERE guest_session_id = :guest_session_id
              AND (requested_by_user_id IS NULL OR requested_by_user_id <> :uid)
            """,
            {"uid": user_id, "guest_session_id": guest_session_id},
        )

        # Keep intake sessions tied to the authenticated account for resume/rehydration.
        merge_summary["intake_sessions"] = _update_if_exists(
            db,
            """
            UPDATE projects.intake_sessions
            SET user_id = :uid,
                updated_at = now()
            WHERE guest_session_id = :guest_session_id
              AND (user_id IS NULL OR user_id <> :uid)
            """,
            {"uid": user_id, "guest_session_id": guest_session_id},
        )

        # Drawing assets may or may not be present depending on schema state.
        try:
            merge_summary["drawing_assets"] = _update_if_exists(
                db,
                """
                UPDATE sourcing.drawing_assets
                SET created_by_user_id = :uid,
                    updated_at = now()
                WHERE bom_id IN (
                    SELECT id FROM bom.boms WHERE guest_session_id = :guest_session_id
                )
                  AND (created_by_user_id IS NULL OR created_by_user_id <> :uid)
                """,
                {"uid": user_id, "guest_session_id": guest_session_id},
            )
        except Exception:
            merge_summary["drawing_assets"] = 0

        merge_summary["guest_sessions"] = _update_if_exists(
            db,
            """
            UPDATE auth.guest_sessions
            SET merged_user_id = :uid,
                merged_at = now(),
                updated_at = now()
            WHERE session_token = :token
              AND (merged_user_id IS NULL OR merged_user_id <> :uid)
            """,
            {"uid": user_id, "token": session_token},
        )

        # Preserve BOM -> Project links and Analysis -> Project lineage.
        _update_if_exists(
            db,
            """
            UPDATE bom.boms b
            SET project_id = p.id,
                updated_at = now()
            FROM projects.projects p
            WHERE p.bom_id = b.id
              AND b.guest_session_id = :guest_session_id
              AND (b.project_id IS NULL OR b.project_id <> p.id)
            """,
            {"guest_session_id": guest_session_id},
        )

        _update_if_exists(
            db,
            """
            UPDATE bom.analysis_results a
            SET project_id = p.id,
                updated_at = now()
            FROM bom.boms b
            JOIN projects.projects p ON p.bom_id = b.id
            WHERE a.bom_id = b.id
              AND b.guest_session_id = :guest_session_id
              AND (a.project_id IS NULL OR a.project_id <> p.id)
            """,
            {"guest_session_id": guest_session_id},
        )

        db.commit()
        return merge_summary

    except Exception as exc:
        db.rollback()
        raise RuntimeError(f"Guest merge failed: {exc}") from exc


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

    try:
        db.add(user)
        # P-3: flush (not commit) so user.id is available for merge,
        # but the row is not yet visible to other transactions.
        db.flush()
        db.refresh(user)

        merge_result = attach_guest_boms(db, user.id, body.session_token)

        # P-3: single commit for both user creation and guest merge
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {exc}")

    token = create_access_token({"sub": user.id, "email": user.email})

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_verified=user.is_verified,
        ),
        merge_result=GuestMergeResult.model_validate(merge_result),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    try:
        merge_result = attach_guest_boms(db, user.id, body.session_token)
        # P-3: commit merge in same transaction as login validation
        db.commit()
    except Exception:
        db.rollback()
        merge_result = {"status": "skipped", "reason": "merge_failed", "merged": False}

    token = create_access_token({"sub": user.id, "email": user.email})

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_verified=user.is_verified,
        ),
        merge_result=GuestMergeResult.model_validate(merge_result),
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
