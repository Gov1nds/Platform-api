"""Organization, workspace, and membership schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class OrganizationCreate(BaseModel):
    name: str
    slug: str
    default_currency: str = "USD"
    default_region: Optional[str] = None


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    default_currency: Optional[str] = None
    default_region: Optional[str] = None
    logo_url: Optional[str] = None


class OrganizationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: str
    owner_user_id: Optional[str] = None
    default_currency: str = "USD"
    default_region: Optional[str] = None
    logo_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WorkspaceCreate(BaseModel):
    name: str
    description: Optional[str] = None
    default_currency: Optional[str] = None
    default_region: Optional[str] = None
    budget_limit: Optional[int] = None


class WorkspaceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    name: str
    description: Optional[str] = None
    status: str
    default_currency: Optional[str] = None
    default_region: Optional[str] = None
    budget_limit: Optional[int] = None
    created_at: Optional[datetime] = None
    member_count: int = 0


class MemberInvite(BaseModel):
    email: str
    role: str = "viewer"


class MemberUpdate(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None


class MemberSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    user_id: str
    role: str
    status: str
    invited_email: Optional[str] = None
    joined_at: Optional[datetime] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
