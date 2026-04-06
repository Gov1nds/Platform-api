from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas import UserRegister, UserLogin, TokenResponse, UserResponse, VendorUserLogin, VendorTokenResponse, VendorUserResponse
from app.models.user import User, GuestSession, VendorUser
from app.utils.dependencies import require_user
from app.services.event_service import track

router = APIRouter(prefix="/auth", tags=["Auth"])

def _merge_guest(db: Session, user_id: str, session_token: str|None) -> dict:
    if not session_token: return {"merged":False}
    guest = db.query(GuestSession).filter(GuestSession.session_token == session_token).first()
    if not guest: return {"merged":False}
    gid = guest.id
    tables = [
        ("bom.boms","uploaded_by_user_id","guest_session_id"),
        ("projects.projects","user_id","guest_session_id"),
        ("bom.analysis_results","user_id","guest_session_id"),
        ("sourcing.rfq_batches","requested_by_user_id","guest_session_id"),
        ("projects.search_sessions","user_id","guest_session_id"),
        ("projects.sourcing_cases","user_id","guest_session_id"),
    ]
    counts = {}
    for tbl,uid_col,gsid_col in tables:
        try:
            r = db.execute(text(f"UPDATE {tbl} SET {uid_col}=:uid, updated_at=now() WHERE {gsid_col}=:gid AND ({uid_col} IS NULL OR {uid_col}!=:uid)"),{"uid":user_id,"gid":gid})
            counts[tbl.split(".")[-1]] = r.rowcount
        except Exception: counts[tbl.split(".")[-1]] = 0
    try: db.execute(text("UPDATE auth.guest_sessions SET merged_user_id=:uid, merged_at=now() WHERE id=:gid"),{"uid":user_id,"gid":gid})
    except Exception: pass
    # Fix project visibility and ACL
    try: db.execute(text("UPDATE projects.projects SET visibility='owner_only' WHERE user_id=:uid AND visibility='guest_preview'"),{"uid":user_id})
    except Exception: pass
    # Grant owner ACL on merged projects
    try:
        db.execute(text(
            "INSERT INTO projects.project_acl (id,project_id,principal_type,principal_id,role) "
            "SELECT gen_random_uuid()::text, id, 'user', :uid, 'owner' FROM projects.projects WHERE user_id=:uid "
            "ON CONFLICT DO NOTHING"
        ),{"uid":user_id})
    except Exception: pass
    return {"merged":True,"counts":counts}

@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(email=body.email, password_hash=hash_password(body.password), full_name=body.full_name)
    db.add(user); db.flush(); db.refresh(user)
    merge = _merge_guest(db, user.id, body.session_token)
    track(db,"signup",actor_id=user.id,resource_type="user",resource_id=user.id)
    db.commit()
    token = create_access_token({"sub":user.id,"email":user.email,"role":user.role,"type":"buyer"})
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user), merge_result=merge)

@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    merge = _merge_guest(db, user.id, body.session_token)
    db.commit()
    token = create_access_token({"sub":user.id,"email":user.email,"role":user.role,"type":"buyer"})
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user), merge_result=merge)

@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(require_user)):
    return UserResponse.model_validate(user)

@router.post("/vendor/login", response_model=VendorTokenResponse)
def vendor_login(body: VendorUserLogin, db: Session = Depends(get_db)):
    vu = db.query(VendorUser).filter(VendorUser.email == body.email).first()
    if not vu or not verify_password(body.password, vu.password_hash):
        raise HTTPException(401, "Invalid vendor credentials")
    token = create_access_token({"sub":vu.id,"email":vu.email,"type":"vendor","vendor_id":vu.vendor_id})
    return VendorTokenResponse(access_token=token, user=VendorUserResponse.model_validate(vu))
