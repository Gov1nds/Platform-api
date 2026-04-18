from sqlalchemy import Column, Text, DateTime, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    from sqlalchemy import LargeBinary as Vector  # fallback

class PartMaster(Base):
    __tablename__ = "part_master"
    part_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    canonical_name = Column(Text, nullable=False)
    category = Column(Text, nullable=False, server_default="unknown")
    commodity_group = Column(Text)
    taxonomy_code = Column(Text)
    spec_template = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    default_uom = Column(Text)
    search_tokens = Column(Text)
    classification_confidence = Column(Numeric(5, 4))
    manufacturer_part_number = Column(Text)
    manufacturer = Column(Text)
    canonical_sku_id = Column(UUID(as_uuid=True))
    embedding = Column(Vector(384)) if callable(getattr(Vector, "__init__", None)) else Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
