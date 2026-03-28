"""Drawing model — maps to sourcing.drawing_assets and drawing_asset_versions."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, BigInteger, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class DrawingAsset(Base):
    __tablename__ = "drawing_assets"
    __table_args__ = {"schema": "sourcing"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    bom_part_id = Column(UUID(as_uuid=False), ForeignKey("bom.bom_parts.id", ondelete="SET NULL"), nullable=True)
    rfq_item_id = Column(UUID(as_uuid=False), ForeignKey("sourcing.rfq_items.id", ondelete="SET NULL"), nullable=True)
    rfq_batch_id = Column(UUID(as_uuid=False), nullable=True)  # For relationship, not formal FK
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True)
    storage_provider = Column(Text, nullable=False, default="railway_object_storage")
    storage_path = Column(Text, nullable=False, default="")
    file_name = Column(Text, nullable=False, default="drawing")
    mime_type = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    file_hash = Column(Text, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def rfq_id(self):
        return self.rfq_batch_id

    @rfq_id.setter
    def rfq_id(self, value):
        self.rfq_batch_id = value

    @property
    def original_filename(self):
        return self.file_name

    @original_filename.setter
    def original_filename(self, value):
        self.file_name = value or "drawing"

    @property
    def stored_filename(self):
        return self.storage_path.rsplit("/", 1)[-1] if self.storage_path else ""

    @property
    def file_format(self):
        return self.mime_type.split("/")[-1] if self.mime_type else ""

    @property
    def user_id(self):
        return self.created_by_user_id

    @property
    def part_name(self):
        return (getattr(self, "_extra", None) or {}).get("part_name", "")

    @property
    def part_notes(self):
        return (getattr(self, "_extra", None) or {}).get("part_notes", "")

    @property
    def status(self):
        return (getattr(self, "_extra", None) or {}).get("status", "received")

    rfq = relationship("RFQBatch", back_populates="drawings",
                        foreign_keys=[rfq_batch_id],
                        primaryjoin="DrawingAsset.rfq_batch_id == RFQBatch.id")


# Alias
Drawing = DrawingAsset
