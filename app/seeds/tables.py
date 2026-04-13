from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

seed_metadata = MetaData()

countries_currencies = Table(
    "countries_currencies",
    seed_metadata,
    Column("country_code", String(3), primary_key=True),
    Column("country_name", Text, nullable=False),
    Column("default_currency", String(3), nullable=False),
    Column("region_group", Text, nullable=True),
    schema="reference",
)

manufacturing_processes = Table(
    "manufacturing_processes",
    seed_metadata,
    Column("process_code", String(120), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("process_family", Text, nullable=True),
    schema="reference",
)

material_families = Table(
    "material_families",
    seed_metadata,
    Column("material_family", Text, primary_key=True),
    Column("examples", JSONB, nullable=False, default=list),
    schema="reference",
)

category_taxonomy = Table(
    "category_taxonomy",
    seed_metadata,
    Column("taxonomy_code", String(120), primary_key=True),
    Column("taxonomy_path", Text, nullable=False),
    Column("commodity_group", Text, nullable=True),
    Column("hs_code_default", String(32), nullable=True),
    Column("unit_of_measure_options", JSONB, nullable=False, default=list),
    Column("spec_schema_json", JSONB, nullable=False, default=dict),
    Column("model_version", String(80), nullable=True),
    Column("data_source", String(80), nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("version", Integer, nullable=False, default=1),
    schema="reference",
)

unit_catalog = Table(
    "unit_catalog",
    seed_metadata,
    Column("unit_code", String(40), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("aliases", JSONB, nullable=False, default=list),
    schema="reference",
)

incoterms = Table(
    "incoterms",
    seed_metadata,
    Column("incoterm_code", String(16), primary_key=True),
    Column("description", Text, nullable=True),
    schema="reference",
)

shipping_modes = Table(
    "shipping_modes",
    seed_metadata,
    Column("mode", String(40), primary_key=True),
    Column("description", Text, nullable=True),
    schema="reference",
)

certification_types = Table(
    "certification_types",
    seed_metadata,
    Column("cert_type_code", String(64), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("category", Text, nullable=True),
    Column("requires_expiry", Boolean, nullable=False, default=False),
    schema="reference",
)

notification_event_types = Table(
    "notification_event_types",
    seed_metadata,
    Column("event_type", String(80), primary_key=True),
    Column("channels", JSONB, nullable=False, default=list),
    schema="reference",
)

vendor_seed_catalog = Table(
    "vendor_seed_catalog",
    seed_metadata,
    Column("vendor_id", String(36), primary_key=True),
    Column("name", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
    schema="pricing",
)

vendor_capability_seed_catalog = Table(
    "vendor_capability_seed_catalog",
    seed_metadata,
    Column("capability_id", String(36), primary_key=True),
    Column("vendor_id", String(36), nullable=False),
    Column("taxonomy_code", String(120), nullable=True),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
    schema="pricing",
)
