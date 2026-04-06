import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Text, DateTime, ForeignKey, Numeric, Boolean, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        Index("ix_shipments_po", "po_id"),
        {"schema": "logistics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    po_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.purchase_orders.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=False), nullable=True)
    carrier = Column(Text, nullable=True)
    tracking_number = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="created")
    origin = Column(Text, nullable=True)
    destination = Column(Text, nullable=True)
    eta = Column(DateTime(timezone=True), nullable=True)
    actual_delivery = Column(DateTime(timezone=True), nullable=True)
    shipment_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    milestones = relationship("ShipmentMilestone", back_populates="shipment", cascade="all, delete-orphan")


class ShipmentMilestone(Base):
    __tablename__ = "shipment_milestones"
    __table_args__ = (
        Index("ix_sm_shipment", "shipment_id"),
        {"schema": "logistics"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("logistics.shipments.id", ondelete="CASCADE"), nullable=False)
    milestone_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="completed")
    location = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(Text, nullable=True)
    is_delay = Column(Boolean, nullable=False, default=False)
    attachment_url = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), default=_now)
    created_at = Column(DateTime(timezone=True), default=_now)

    shipment = relationship("Shipment", back_populates="milestones")
