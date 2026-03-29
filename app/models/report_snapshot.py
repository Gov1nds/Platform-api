"""Report snapshot model — maps to projects.report_snapshots.

Stores a complete, immutable snapshot of every report version
so any historical report can be reconstructed exactly.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base


class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"
    __table_args__ = (
        Index("ix_report_snap_project", "project_id"),
        Index("ix_report_snap_version", "project_id", "version"),
        {"schema": "projects"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    report_json = Column(JSONB, nullable=False, default=dict)
    strategy_json = Column(JSONB, nullable=False, default=dict)
    procurement_json = Column(JSONB, nullable=False, default=dict)
    analyzer_version = Column(Text, nullable=True)
    classifier_version = Column(Text, nullable=True)
    normalizer_version = Column(Text, nullable=True)
    source_checksum = Column(Text, nullable=True)
    replay_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
