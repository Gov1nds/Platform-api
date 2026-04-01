"""Models package — re-exports for convenience.

Updated for PostgreSQL schema. Provides backward-compatible aliases
so any code doing `from app.models import BOMStatus` still works.
"""
from app.models.user import User, GuestSession
from app.models.project import Project, ProjectEvent
from app.models.bom import BOM, BOMPart
from app.models.analysis import AnalysisResult
from app.models.vendor import Vendor, VendorCapability
from app.models.vendor_match import VendorMatch, VendorMatchRun
from app.models.pricing import PricingQuote, PricingHistory  # PricingHistory = PricingQuote alias
from app.models.rfq import RFQBatch, RFQItem, RFQQuote, RFQStatus
from app.models.tracking import ProductionTracking, ExecutionFeedback, TrackingStage
from app.models.memory import SupplierMemory, SupplierMemoryHistory
from app.models.drawing import DrawingAsset
from app.models.catalog import PartMaster, PartAlias, PartAttribute, PartObservation, ReviewQueueItem
from app.models.geo import Country, RegionProfile, ExchangeRate, TariffRule
from app.models.report_snapshot import ReportSnapshot
from app.models.strategy_run import StrategyRun

# Backward-compat aliases for removed/renamed items
RFQ = RFQBatch
Drawing = DrawingAsset


class BOMStatus:
    """Backward-compat shim. The PostgreSQL schema uses plain text status values."""
    uploaded = "uploaded"
    analyzed = "analyzed"
    completed = "completed"
    parsed = "parsed"
    analyzing = "analyzing"
    enriched = "enriched"
    quoted = "quoted"
    archived = "archived"
    error = "error"


class CostSavings:
    """Backward-compat shim. Cost savings data is now stored in
    AnalysisResult.structured_output JSONB, not a separate table.
    """
    def __init__(self, **kwargs):
        self.analysis_id = kwargs.get("analysis_id")
        self.recommended_cost = kwargs.get("recommended_cost", 0)
        self.alternative_cost = kwargs.get("alternative_cost", 0)
        self.savings_percent = kwargs.get("savings_percent", 0)
        self.savings_value = kwargs.get("savings_value", 0)
