from sqlalchemy import Column, Text, DateTime, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB, UUID, INET
from app.core.database import Base

class GuestSearchLog(Base):
    __tablename__ = "guest_search_log"
    search_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id = Column(UUID(as_uuid=True), nullable=False)
    search_query = Column(Text, nullable=False)
    components_json = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    detected_country = Column(Text)
    detected_currency = Column(Text)
    delivery_location_json = Column(JSONB)
    vendor_results_json = Column(JSONB)
    free_report_generated = Column(Boolean, default=False)
    converted_to_signup = Column(Boolean, default=False)
    converted_user_id = Column(UUID(as_uuid=True))
    ip_address = Column(INET)
    user_agent = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
