"""User model — maps to auth.users in PostgreSQL."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(Text, nullable=True)
    role = Column(Text, nullable=False, default="user")
    status = Column(Text, nullable=False, default="active")
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Convenience properties for backward compat
    @property
    def is_active(self):
        return self.status == "active"

    @property
    def is_verified(self):
        return self.email_verified_at is not None

    boms = relationship("BOM", back_populates="user", foreign_keys="BOM.uploaded_by_user_id")
    projects = relationship("Project", back_populates="user")
    rfqs = relationship("RFQBatch", back_populates="user")


class GuestSession(Base):
    """Maps to auth.guest_sessions."""
    __tablename__ = "guest_sessions"
    __table_args__ = {"schema": "auth"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_token = Column(Text, nullable=False, unique=True)
    first_seen_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    merged_user_id = Column(UUID(as_uuid=False), nullable=True)
    merged_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
