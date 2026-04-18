from sqlalchemy import Column, Text, DateTime, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base

class DataFreshnessLog(Base):
    __tablename__ = "data_freshness_log"
    log_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    table_name = Column(Text, nullable=False)
    record_id = Column(Text, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    source_api = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    previous_value_json = Column(JSONB, nullable=True)
    new_value_json = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
