from sqlalchemy import Column, Text, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base

class VendorInviteToken(Base):
    __tablename__ = "vendor_invite_token"
    token_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    vendor_id = Column(UUID(as_uuid=True), nullable=False)
    email = Column(Text, nullable=False)
    invited_by_user_id = Column(UUID(as_uuid=True))
    token_hash = Column(Text, nullable=False, unique=True)
    purpose = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
