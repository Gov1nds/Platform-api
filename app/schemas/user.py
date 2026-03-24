"""User schemas."""

from pydantic import BaseModel, EmailStr
from typing import Optional


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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse