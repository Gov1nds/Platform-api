from sqlalchemy import Column, Text, DateTime, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base

class ApprovalChain(Base):
    __tablename__ = "approval_chain"
    chain_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    organization_id = Column(UUID(as_uuid=True), nullable=False)
    name = Column(Text, nullable=False)
    rules_json = Column(JSONB, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
