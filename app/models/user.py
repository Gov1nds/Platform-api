import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email", unique=True), {"schema": "auth"})

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    email = Column(String(320), nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False, default="")
    role = Column(String(40), nullable=False, default="buyer")
    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    permissions = Column(JSONB, nullable=False, default=list)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    projects = relationship("Project", back_populates="user")
    boms = relationship("BOM", back_populates="user")
    rfqs = relationship("RFQBatch", back_populates="user")


class GuestSession(Base):
    __tablename__ = "guest_sessions"
    __table_args__ = (Index("ix_guest_sessions_token", "session_token", unique=True), {"schema": "auth"})

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    session_token = Column(String(120), nullable=False, unique=True)
    merged_user_id = Column(UUID(as_uuid=False), nullable=True)
    merged_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class VendorUser(Base):
    __tablename__ = "vendor_users"
    __table_args__ = (Index("ix_vendor_users_email", "email", unique=True), {"schema": "auth"})

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    vendor_id = Column(UUID(as_uuid=False), nullable=False, index=True)
    email = Column(String(320), nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False, default="")
    role = Column(String(40), nullable=False, default="vendor_user")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
