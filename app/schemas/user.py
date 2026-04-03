"""User schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    session_token: Optional[str] = None   # 🔥 FIXED


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    session_token: Optional[str] = None   # 🔥 FIXED


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    is_active: bool = True
    is_verified: bool = False

    class Config:
        from_attributes = True


class GuestMergeResult(BaseModel):
    session_token: Optional[str] = None
    guest_session_id: Optional[str] = None
    user_id: Optional[str] = None
    status: str = "noop"
    merged_project_ids: List[str] = Field(default_factory=list)
    merged_bom_ids: List[str] = Field(default_factory=list)
    merged_analysis_ids: List[str] = Field(default_factory=list)
    merged_rfq_ids: List[str] = Field(default_factory=list)
    merged_drawing_ids: List[str] = Field(default_factory=list)
    unlock_state: Optional[Dict[str, Any]] = None
    merged_counts: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse