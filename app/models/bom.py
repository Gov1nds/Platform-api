"""BOM and BOMPart models."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


class BOMStatus(str, enum.Enum):
    uploaded = "uploaded"
    analyzed = "analyzed"
    completed = "completed"


class BOM(Base):
    __tablename__ = "boms"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_token = Column(String(64), nullable=True, index=True)
    name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    file_name = Column(String(255), nullable=True)
    file_type = Column(String(20), nullable=True)
    raw_data = Column(JSON, nullable=True)
    total_parts = Column(Integer, default=0)
    status = Column(String(20), default=BOMStatus.uploaded.value)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="boms")
    parts = relationship("BOMPart", back_populates="bom", cascade="all, delete-orphan")
    analysis = relationship("AnalysisResult", back_populates="bom", uselist=False, cascade="all, delete-orphan")
    project = relationship("Project", back_populates="bom", uselist=False, cascade="all, delete-orphan")
    rfqs = relationship("RFQ", back_populates="bom")


class BOMPart(Base):
    __tablename__ = "bom_parts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(String(36), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False, index=True)
    part_name = Column(String(500), nullable=True)
    material = Column(String(255), nullable=True)
    quantity = Column(Integer, default=1)
    geometry_type = Column(String(50), nullable=True)
    dimensions = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    manufacturer = Column(String(255), nullable=True)
    mpn = Column(String(255), nullable=True)
    category = Column(String(50), nullable=True)
    specs = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    bom = relationship("BOM", back_populates="parts")
