"""BOM and BOMPart models — maps to bom.boms and bom.bom_parts in PostgreSQL."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Boolean, Numeric, BigInteger
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class BOM(Base):
    __tablename__ = "boms"
    __table_args__ = {"schema": "bom"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    uploaded_by_user_id = Column(UUID(as_uuid=False), ForeignKey("auth.users.id", ondelete="SET NULL"), nullable=True)
    guest_session_id = Column(UUID(as_uuid=False), ForeignKey("auth.guest_sessions.id", ondelete="SET NULL"), nullable=True)
    project_id = Column(UUID(as_uuid=False), nullable=True)  # FK added by ALTER in bootstrap
    source_file_name = Column(Text, nullable=False, default="upload.csv")
    source_file_type = Column(Text, nullable=False, default="csv")
    source_checksum = Column(Text, nullable=True)
    source_uri = Column(Text, nullable=True)
    original_filename = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    name = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    delivery_country_id = Column(UUID(as_uuid=False), nullable=True)
    target_currency = Column(String(3), nullable=False, default="USD")
    priority = Column(Text, nullable=False, default="balanced")
    status = Column(Text, nullable=False, default="uploaded")
    raw_payload = Column(JSONB, nullable=False, default=dict)
    parse_summary = Column(JSONB, nullable=False, default=dict)
    total_parts = Column(Integer, nullable=False, default=0)
    total_custom_parts = Column(Integer, nullable=False, default=0)
    total_standard_parts = Column(Integer, nullable=False, default=0)
    total_raw_parts = Column(Integer, nullable=False, default=0)
    model_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def user_id(self):
        return self.uploaded_by_user_id

    @user_id.setter
    def user_id(self, value):
        self.uploaded_by_user_id = value

    @property
    def file_name(self):
        return self.source_file_name

    @file_name.setter
    def file_name(self, value):
        self.source_file_name = value or "upload.csv"

    @property
    def file_type(self):
        return self.source_file_type

    @file_type.setter
    def file_type(self, value):
        self.source_file_type = value or "csv"

    @property
    def raw_data(self):
        return self.raw_payload

    @raw_data.setter
    def raw_data(self, value):
        self.raw_payload = value or {}

    @property
    def session_token(self):
        """Retrieve token from guest session if linked."""
        return getattr(self, "_session_token_cache", None)

    @session_token.setter
    def session_token(self, value):
        self._session_token_cache = value

    user = relationship("User", back_populates="boms", foreign_keys=[uploaded_by_user_id])
    parts = relationship("BOMPart", back_populates="bom", cascade="all, delete-orphan")
    analysis = relationship("AnalysisResult", back_populates="bom", uselist=False, cascade="all, delete-orphan")
    rfqs = relationship("RFQBatch", back_populates="bom")


class BOMPart(Base):
    __tablename__ = "bom_parts"
    __table_args__ = {"schema": "bom"}

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    bom_id = Column(UUID(as_uuid=False), ForeignKey("bom.boms.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Text, nullable=False, default="")
    raw_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    canonical_name = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(18, 6), nullable=False, default=1)
    unit = Column(Text, nullable=True)
    part_number = Column(Text, nullable=True)
    mpn = Column(Text, nullable=True)
    manufacturer = Column(Text, nullable=True)
    supplier_name = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=False), nullable=True)
    category_code = Column(Text, nullable=True)
    procurement_class = Column(Text, nullable=False, default="unknown")
    material = Column(Text, nullable=True)
    material_family = Column(Text, nullable=True)
    material_grade = Column(Text, nullable=True)
    material_form = Column(Text, nullable=True)
    geometry = Column(Text, nullable=True)
    tolerance = Column(Text, nullable=True)
    finish = Column(Text, nullable=True)
    operation_type = Column(Text, nullable=True)
    process_hint = Column(Text, nullable=True)
    secondary_ops = Column(JSONB, nullable=False, default=list)
    specs = Column(JSONB, nullable=False, default=dict)
    classification_confidence = Column(Numeric(12, 6), nullable=False, default=0)
    classification_reason = Column(Text, nullable=True)
    classification_path = Column(JSONB, nullable=False, default=list)
    has_mpn = Column(Boolean, nullable=False, default=False)
    has_brand = Column(Boolean, nullable=False, default=False)
    is_generic = Column(Boolean, nullable=False, default=False)
    is_raw = Column(Boolean, nullable=False, default=False)
    is_custom = Column(Boolean, nullable=False, default=False)
    rfq_required = Column(Boolean, nullable=False, default=False)
    drawing_required = Column(Boolean, nullable=False, default=False)
    risk_level = Column(Text, nullable=False, default="medium")
    source_row = Column(Integer, nullable=True)
    source_row_hash = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Backward-compat aliases
    @property
    def part_name(self):
        return self.canonical_name or self.description or self.normalized_text or ""

    @part_name.setter
    def part_name(self, value):
        self.canonical_name = value

    @property
    def category(self):
        return self.category_code or self.procurement_class or ""

    @category.setter
    def category(self, value):
        self.category_code = value

    @property
    def geometry_type(self):
        return self.geometry

    @geometry_type.setter
    def geometry_type(self, value):
        self.geometry = value

    @property
    def notes(self):
        return self.raw_text or ""

    @notes.setter
    def notes(self, value):
        self.raw_text = value

    @property
    def part_type(self):
        return "custom" if self.is_custom else "standard"

    bom = relationship("BOM", back_populates="parts")
