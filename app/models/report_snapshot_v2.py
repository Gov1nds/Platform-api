from sqlalchemy import Column, Text, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base

class ReportSnapshotV2(Base):
    __tablename__ = "report_snapshot"
    __table_args__ = {"extend_existing": True}
    snapshot_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    organization_id = Column(UUID(as_uuid=True), nullable=False)
    report_type = Column(Text, nullable=False)
    period_start = Column(DateTime(timezone=True))
    period_end = Column(DateTime(timezone=True))
    filters_json = Column(JSONB, server_default=text("'{}'::jsonb"))
    payload_json = Column(JSONB, nullable=False)
    rendered_pdf_url = Column(Text)
    rendered_xlsx_url = Column(Text)
    ai_insight_text = Column(Text)
    generated_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
