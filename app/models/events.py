import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Text, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class PlatformEvent(Base):
    __tablename__ = "platform_events"
    __table_args__ = {"schema": "ops"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=False), nullable=True)
    actor_type = Column(Text, nullable=False, default="user")
    resource_type = Column(Text, nullable=True)
    resource_id = Column(UUID(as_uuid=False), nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)


class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"
    __table_args__ = {"schema": "ops"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    report_type = Column(Text, nullable=False)
    scope_type = Column(Text, nullable=True)
    scope_id = Column(UUID(as_uuid=False), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    filters_json = Column(JSONB, nullable=False, default=dict)
    data_json = Column(JSONB, nullable=False, default=dict)
    summary_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
