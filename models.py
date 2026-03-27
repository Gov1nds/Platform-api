from typing import Optional
import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Double, ForeignKeyConstraint, Index, Integer, JSON, Numeric, PrimaryKeyConstraint, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


t_pg_stat_statements = Table(
    'pg_stat_statements', Base.metadata,
    Column('userid', OID),
    Column('dbid', OID),
    Column('toplevel', Boolean),
    Column('queryid', BigInteger),
    Column('query', Text),
    Column('plans', BigInteger),
    Column('total_plan_time', Double(53)),
    Column('min_plan_time', Double(53)),
    Column('max_plan_time', Double(53)),
    Column('mean_plan_time', Double(53)),
    Column('stddev_plan_time', Double(53)),
    Column('calls', BigInteger),
    Column('total_exec_time', Double(53)),
    Column('min_exec_time', Double(53)),
    Column('max_exec_time', Double(53)),
    Column('mean_exec_time', Double(53)),
    Column('stddev_exec_time', Double(53)),
    Column('rows', BigInteger),
    Column('shared_blks_hit', BigInteger),
    Column('shared_blks_read', BigInteger),
    Column('shared_blks_dirtied', BigInteger),
    Column('shared_blks_written', BigInteger),
    Column('local_blks_hit', BigInteger),
    Column('local_blks_read', BigInteger),
    Column('local_blks_dirtied', BigInteger),
    Column('local_blks_written', BigInteger),
    Column('temp_blks_read', BigInteger),
    Column('temp_blks_written', BigInteger),
    Column('shared_blk_read_time', Double(53)),
    Column('shared_blk_write_time', Double(53)),
    Column('local_blk_read_time', Double(53)),
    Column('local_blk_write_time', Double(53)),
    Column('temp_blk_read_time', Double(53)),
    Column('temp_blk_write_time', Double(53)),
    Column('wal_records', BigInteger),
    Column('wal_fpi', BigInteger),
    Column('wal_bytes', Numeric),
    Column('wal_buffers_full', BigInteger),
    Column('jit_functions', BigInteger),
    Column('jit_generation_time', Double(53)),
    Column('jit_inlining_count', BigInteger),
    Column('jit_inlining_time', Double(53)),
    Column('jit_optimization_count', BigInteger),
    Column('jit_optimization_time', Double(53)),
    Column('jit_emission_count', BigInteger),
    Column('jit_emission_time', Double(53)),
    Column('jit_deform_count', BigInteger),
    Column('jit_deform_time', Double(53)),
    Column('parallel_workers_to_launch', BigInteger),
    Column('parallel_workers_launched', BigInteger),
    Column('stats_since', DateTime(True)),
    Column('minmax_stats_since', DateTime(True))
)


t_pg_stat_statements_info = Table(
    'pg_stat_statements_info', Base.metadata,
    Column('dealloc', BigInteger),
    Column('stats_reset', DateTime(True))
)


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='users_pkey'),
        Index('ix_users_email', 'email', unique=True)
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_verified: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    boms: Mapped[list['Boms']] = relationship('Boms', back_populates='user')
    analysis_results: Mapped[list['AnalysisResults']] = relationship('AnalysisResults', back_populates='user')
    projects: Mapped[list['Projects']] = relationship('Projects', back_populates='user')
    rfqs: Mapped[list['Rfqs']] = relationship('Rfqs', back_populates='user')


class Vendors(Base):
    __tablename__ = 'vendors'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='vendors_pkey'),
        Index('ix_vendors_country', 'country')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(100))
    region: Mapped[Optional[str]] = mapped_column(String(50))
    capabilities: Mapped[Optional[dict]] = mapped_column(JSON)
    rating: Mapped[Optional[float]] = mapped_column(Double(53))
    reliability_score: Mapped[Optional[float]] = mapped_column(Double(53))
    avg_lead_time: Mapped[Optional[float]] = mapped_column(Double(53))
    contact_email: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    pricing_history: Mapped[list['PricingHistory']] = relationship('PricingHistory', back_populates='vendor')
    supplier_memory: Mapped[list['SupplierMemory']] = relationship('SupplierMemory', back_populates='vendor')
    rfqs: Mapped[list['Rfqs']] = relationship('Rfqs', back_populates='selected_vendor')
    rfq_quotes: Mapped[list['RfqQuotes']] = relationship('RfqQuotes', back_populates='vendor')


class Boms(Base):
    __tablename__ = 'boms'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='boms_user_id_fkey'),
        PrimaryKeyConstraint('id', name='boms_pkey'),
        Index('ix_boms_session_token', 'session_token'),
        Index('ix_boms_user_id', 'user_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    session_token: Mapped[Optional[str]] = mapped_column(String(64))
    name: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    file_type: Mapped[Optional[str]] = mapped_column(String(20))
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    total_parts: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    user: Mapped[Optional['Users']] = relationship('Users', back_populates='boms')
    analysis_results: Mapped[list['AnalysisResults']] = relationship('AnalysisResults', back_populates='bom')
    bom_parts: Mapped[list['BomParts']] = relationship('BomParts', back_populates='bom')
    projects: Mapped[list['Projects']] = relationship('Projects', back_populates='bom')
    rfqs: Mapped[list['Rfqs']] = relationship('Rfqs', back_populates='bom')
    drawing_assets: Mapped[list['DrawingAssets']] = relationship('DrawingAssets', back_populates='bom')


class PricingHistory(Base):
    __tablename__ = 'pricing_history'
    __table_args__ = (
        ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE', name='pricing_history_vendor_id_fkey'),
        PrimaryKeyConstraint('id', name='pricing_history_pkey'),
        Index('ix_pricing_history_vendor_id', 'vendor_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    vendor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    price: Mapped[float] = mapped_column(Double(53), nullable=False)
    part_name: Mapped[Optional[str]] = mapped_column(String(500))
    material: Mapped[Optional[str]] = mapped_column(String(255))
    process: Mapped[Optional[str]] = mapped_column(String(100))
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    region: Mapped[Optional[str]] = mapped_column(String(50))
    recorded_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    vendor: Mapped['Vendors'] = relationship('Vendors', back_populates='pricing_history')


class SupplierMemory(Base):
    __tablename__ = 'supplier_memory'
    __table_args__ = (
        ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='CASCADE', name='supplier_memory_vendor_id_fkey'),
        PrimaryKeyConstraint('id', name='supplier_memory_pkey'),
        Index('ix_supplier_memory_vendor_id', 'vendor_id', unique=True)
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    vendor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    performance_score: Mapped[Optional[float]] = mapped_column(Double(53))
    cost_accuracy_score: Mapped[Optional[float]] = mapped_column(Double(53))
    delivery_accuracy_score: Mapped[Optional[float]] = mapped_column(Double(53))
    risk_level: Mapped[Optional[float]] = mapped_column(Double(53))
    total_orders: Mapped[Optional[float]] = mapped_column(Double(53))
    avg_cost_delta_pct: Mapped[Optional[float]] = mapped_column(Double(53))
    avg_lead_delta_days: Mapped[Optional[float]] = mapped_column(Double(53))
    last_updated: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    vendor: Mapped['Vendors'] = relationship('Vendors', back_populates='supplier_memory')


class AnalysisResults(Base):
    __tablename__ = 'analysis_results'
    __table_args__ = (
        ForeignKeyConstraint(['bom_id'], ['boms.id'], ondelete='CASCADE', name='analysis_results_bom_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='analysis_results_user_id_fkey'),
        PrimaryKeyConstraint('id', name='analysis_results_pkey'),
        Index('ix_analysis_results_bom_id', 'bom_id', unique=True)
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bom_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    raw_analyzer_output: Mapped[Optional[dict]] = mapped_column(JSON)
    strategy_output: Mapped[Optional[dict]] = mapped_column(JSON)
    enriched_output: Mapped[Optional[dict]] = mapped_column(JSON)
    recommended_location: Mapped[Optional[str]] = mapped_column(String(100))
    average_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    cost_range_low: Mapped[Optional[float]] = mapped_column(Double(53))
    cost_range_high: Mapped[Optional[float]] = mapped_column(Double(53))
    savings_percent: Mapped[Optional[float]] = mapped_column(Double(53))
    lead_time: Mapped[Optional[float]] = mapped_column(Double(53))
    decision_summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    bom: Mapped['Boms'] = relationship('Boms', back_populates='analysis_results')
    user: Mapped[Optional['Users']] = relationship('Users', back_populates='analysis_results')
    cost_savings: Mapped['CostSavings'] = relationship('CostSavings', uselist=False, back_populates='analysis')


class BomParts(Base):
    __tablename__ = 'bom_parts'
    __table_args__ = (
        ForeignKeyConstraint(['bom_id'], ['boms.id'], ondelete='CASCADE', name='bom_parts_bom_id_fkey'),
        PrimaryKeyConstraint('id', name='bom_parts_pkey'),
        Index('ix_bom_parts_bom_id', 'bom_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bom_id: Mapped[str] = mapped_column(String(36), nullable=False)
    part_name: Mapped[Optional[str]] = mapped_column(String(500))
    material: Mapped[Optional[str]] = mapped_column(String(255))
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    geometry_type: Mapped[Optional[str]] = mapped_column(String(50))
    dimensions: Mapped[Optional[dict]] = mapped_column(JSON)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255))
    mpn: Mapped[Optional[str]] = mapped_column(String(255))
    category: Mapped[Optional[str]] = mapped_column(String(50))
    specs: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    bom: Mapped['Boms'] = relationship('Boms', back_populates='bom_parts')
    drawing_assets: Mapped[list['DrawingAssets']] = relationship('DrawingAssets', back_populates='bom_part')


class Projects(Base):
    __tablename__ = 'projects'
    __table_args__ = (
        ForeignKeyConstraint(['bom_id'], ['boms.id'], ondelete='CASCADE', name='projects_bom_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='projects_user_id_fkey'),
        PrimaryKeyConstraint('id', name='projects_pkey'),
        Index('ix_projects_bom_id', 'bom_id', unique=True),
        Index('ix_projects_status', 'status'),
        Index('ix_projects_user_id', 'user_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bom_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    name: Mapped[Optional[str]] = mapped_column(String(255))
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[Optional[str]] = mapped_column(String(50))
    total_parts: Mapped[Optional[int]] = mapped_column(Integer)
    recommended_location: Mapped[Optional[str]] = mapped_column(String(100))
    average_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    cost_range_low: Mapped[Optional[float]] = mapped_column(Double(53))
    cost_range_high: Mapped[Optional[float]] = mapped_column(Double(53))
    savings_percent: Mapped[Optional[float]] = mapped_column(Double(53))
    lead_time: Mapped[Optional[float]] = mapped_column(Double(53))
    decision_summary: Mapped[Optional[str]] = mapped_column(Text)
    analyzer_report: Mapped[Optional[dict]] = mapped_column(JSON)
    strategy: Mapped[Optional[dict]] = mapped_column(JSON)
    procurement_plan: Mapped[Optional[dict]] = mapped_column(JSON)
    project_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
    rfq_status: Mapped[Optional[str]] = mapped_column(String(30))
    tracking_stage: Mapped[Optional[str]] = mapped_column(String(10))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    bom: Mapped['Boms'] = relationship('Boms', back_populates='projects')
    user: Mapped[Optional['Users']] = relationship('Users', back_populates='projects')
    drawing_assets: Mapped[list['DrawingAssets']] = relationship('DrawingAssets', back_populates='project')
    project_events: Mapped[list['ProjectEvents']] = relationship('ProjectEvents', back_populates='project')


class Rfqs(Base):
    __tablename__ = 'rfqs'
    __table_args__ = (
        ForeignKeyConstraint(['bom_id'], ['boms.id'], ondelete='SET NULL', name='rfqs_bom_id_fkey'),
        ForeignKeyConstraint(['selected_vendor_id'], ['vendors.id'], ondelete='SET NULL', name='rfqs_selected_vendor_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='rfqs_user_id_fkey'),
        PrimaryKeyConstraint('id', name='rfqs_pkey'),
        Index('ix_rfqs_user_id', 'user_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    bom_id: Mapped[Optional[str]] = mapped_column(String(36))
    status: Mapped[Optional[str]] = mapped_column(String(30))
    selected_vendor_id: Mapped[Optional[str]] = mapped_column(String(36))
    total_estimated_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    total_final_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    bom: Mapped[Optional['Boms']] = relationship('Boms', back_populates='rfqs')
    selected_vendor: Mapped[Optional['Vendors']] = relationship('Vendors', back_populates='rfqs')
    user: Mapped[Optional['Users']] = relationship('Users', back_populates='rfqs')
    execution_feedback: Mapped['ExecutionFeedback'] = relationship('ExecutionFeedback', uselist=False, back_populates='rfq')
    production_tracking: Mapped[list['ProductionTracking']] = relationship('ProductionTracking', back_populates='rfq')
    rfq_items: Mapped[list['RfqItems']] = relationship('RfqItems', back_populates='rfq')
    rfq_quotes: Mapped[list['RfqQuotes']] = relationship('RfqQuotes', back_populates='rfq')


class CostSavings(Base):
    __tablename__ = 'cost_savings'
    __table_args__ = (
        ForeignKeyConstraint(['analysis_id'], ['analysis_results.id'], ondelete='CASCADE', name='cost_savings_analysis_id_fkey'),
        PrimaryKeyConstraint('id', name='cost_savings_pkey'),
        UniqueConstraint('analysis_id', name='cost_savings_analysis_id_key')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recommended_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    alternative_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    savings_percent: Mapped[Optional[float]] = mapped_column(Double(53))
    savings_value: Mapped[Optional[float]] = mapped_column(Double(53))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    analysis: Mapped['AnalysisResults'] = relationship('AnalysisResults', back_populates='cost_savings')


class DrawingAssets(Base):
    __tablename__ = 'drawing_assets'
    __table_args__ = (
        ForeignKeyConstraint(['bom_id'], ['boms.id'], ondelete='SET NULL', name='drawing_assets_bom_id_fkey'),
        ForeignKeyConstraint(['bom_part_id'], ['bom_parts.id'], ondelete='SET NULL', name='drawing_assets_bom_part_id_fkey'),
        ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL', name='drawing_assets_project_id_fkey'),
        PrimaryKeyConstraint('id', name='drawing_assets_pkey'),
        Index('ix_drawing_assets_project_id', 'project_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36))
    bom_id: Mapped[Optional[str]] = mapped_column(String(36))
    bom_part_id: Mapped[Optional[str]] = mapped_column(String(36))
    storage_provider: Mapped[Optional[str]] = mapped_column(String(50))
    storage_path: Mapped[Optional[str]] = mapped_column(String(1000))
    file_name: Mapped[Optional[str]] = mapped_column(String(500))
    file_hash: Mapped[Optional[str]] = mapped_column(String(128))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    version: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    bom: Mapped[Optional['Boms']] = relationship('Boms', back_populates='drawing_assets')
    bom_part: Mapped[Optional['BomParts']] = relationship('BomParts', back_populates='drawing_assets')
    project: Mapped[Optional['Projects']] = relationship('Projects', back_populates='drawing_assets')


class ExecutionFeedback(Base):
    __tablename__ = 'execution_feedback'
    __table_args__ = (
        ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ondelete='CASCADE', name='execution_feedback_rfq_id_fkey'),
        PrimaryKeyConstraint('id', name='execution_feedback_pkey'),
        UniqueConstraint('rfq_id', name='execution_feedback_rfq_id_key')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rfq_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predicted_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    actual_cost: Mapped[Optional[float]] = mapped_column(Double(53))
    cost_delta: Mapped[Optional[float]] = mapped_column(Double(53))
    predicted_lead_time: Mapped[Optional[float]] = mapped_column(Double(53))
    actual_lead_time: Mapped[Optional[float]] = mapped_column(Double(53))
    lead_time_delta: Mapped[Optional[float]] = mapped_column(Double(53))
    feedback_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    rfq: Mapped['Rfqs'] = relationship('Rfqs', back_populates='execution_feedback')


class ProductionTracking(Base):
    __tablename__ = 'production_tracking'
    __table_args__ = (
        ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ondelete='CASCADE', name='production_tracking_rfq_id_fkey'),
        PrimaryKeyConstraint('id', name='production_tracking_pkey'),
        Index('ix_production_tracking_rfq_id', 'rfq_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rfq_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(10))
    status_message: Mapped[Optional[str]] = mapped_column(Text)
    progress_percent: Mapped[Optional[int]] = mapped_column(Integer)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    rfq: Mapped['Rfqs'] = relationship('Rfqs', back_populates='production_tracking')


class ProjectEvents(Base):
    __tablename__ = 'project_events'
    __table_args__ = (
        ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE', name='project_events_project_id_fkey'),
        PrimaryKeyConstraint('id', name='project_events_pkey'),
        Index('ix_project_events_project_id', 'project_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    old_status: Mapped[Optional[str]] = mapped_column(String(50))
    new_status: Mapped[Optional[str]] = mapped_column(String(50))
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    project: Mapped['Projects'] = relationship('Projects', back_populates='project_events')


class RfqItems(Base):
    __tablename__ = 'rfq_items'
    __table_args__ = (
        ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ondelete='CASCADE', name='rfq_items_rfq_id_fkey'),
        PrimaryKeyConstraint('id', name='rfq_items_pkey'),
        Index('ix_rfq_items_rfq_id', 'rfq_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rfq_id: Mapped[str] = mapped_column(String(36), nullable=False)
    part_name: Mapped[Optional[str]] = mapped_column(String(500))
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    material: Mapped[Optional[str]] = mapped_column(String(255))
    process: Mapped[Optional[str]] = mapped_column(String(100))
    quoted_price: Mapped[Optional[float]] = mapped_column(Double(53))
    final_price: Mapped[Optional[float]] = mapped_column(Double(53))
    lead_time: Mapped[Optional[float]] = mapped_column(Double(53))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    rfq: Mapped['Rfqs'] = relationship('Rfqs', back_populates='rfq_items')


class RfqQuotes(Base):
    __tablename__ = 'rfq_quotes'
    __table_args__ = (
        ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ondelete='CASCADE', name='rfq_quotes_rfq_id_fkey'),
        ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='SET NULL', name='rfq_quotes_vendor_id_fkey'),
        PrimaryKeyConstraint('id', name='rfq_quotes_pkey'),
        Index('ix_rfq_quotes_rfq_id', 'rfq_id')
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rfq_id: Mapped[str] = mapped_column(String(36), nullable=False)
    vendor_id: Mapped[Optional[str]] = mapped_column(String(36))
    status: Mapped[Optional[str]] = mapped_column(String(30))
    quote_currency: Mapped[Optional[str]] = mapped_column(String(10))
    quote_valid_until: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    quote_received_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    total_quote_value: Mapped[Optional[float]] = mapped_column(Double(53))
    confidence: Mapped[Optional[str]] = mapped_column(String(20))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    rfq: Mapped['Rfqs'] = relationship('Rfqs', back_populates='rfq_quotes')
    vendor: Mapped[Optional['Vendors']] = relationship('Vendors', back_populates='rfq_quotes')
