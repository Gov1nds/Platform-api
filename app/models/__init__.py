"""
Model registry. All models must be imported here so Alembic autogenerate
and Base.metadata.create_all() discover every table.
"""
from app.models.user import (
    User, GuestSession, VendorUser, Organization, OrganizationMembership,
)
from app.models.bom import BOM, BOMPart, AnalysisResult
from app.models.project import (
    Project, ProjectACL, ProjectEvent, SearchSession, SourcingCase,
)
from app.models.vendor import (
    Vendor, VendorCapability, VendorMatchRun, VendorMatch,
    VendorPerformanceSnapshot, VendorImportBatch, VendorImportRow,
    VendorIdentityAlias, VendorEvidenceAttachment,
    VendorLocation, VendorExportCapability, VendorLeadTimeBand,
    VendorCommunicationScore, VendorTrustTier,
)
from app.models.rfq import (
    RFQBatch, RFQItem, RFQVendorInvitation, InvitationStatusEvent,
    RFQQuoteHeader, RFQQuoteLine, PurchaseOrder, POLineItem,
    Invoice, InvoiceLine, Payment, GoodsReceipt, GoodsReceiptLine,
    ApprovalRequest,
)
from app.models.logistics import Shipment, ShipmentMilestone
from app.models.chat import ChatThread, ChatMessage
from app.models.market import (
    FXRate, FreightRate, TariffScopeRegistry, TariffSchedule, CommodityIndex, IntegrationRunLog,
)
from app.models.enrichment import (
    PartToSkuMapping, SKUOffer, SKUOfferPriceBreak, SKUAvailabilitySnapshot,
    HSMapping, LaneScopeRegistry, LaneRateBand, BOMLineDependencyIndex,
    BOMLineEvidenceCoverageFact, EvidenceGapBacklogItem, EnrichmentRunLog,
)
from app.models.canonical import (
    CanonicalSKU, SourceSKULink, ConnectorHealthMetrics,
    CanonicalOfferSnapshot, CanonicalAvailabilitySnapshot,
)
from app.models.events import (
    PlatformEvent, ReportSnapshot, EventAuditLog, IdempotencyRecord,
)
from app.models.notification import Notification, NotificationPreference
from app.models.outcomes import QuoteOutcome, OverrideEvent, LeadTimeHistory, VendorPerformance, AnomalyFlag, ConfidenceCalibrationData
from app.models.matching import PartVendorIndex
from app.models.feedback import RecommendationOverride, LearningEvent
from app.models.market_intelligence import (
    CommodityPriceSignal, VendorLeadTimeHistoryPhase3,
    MarketAnomalyEvent, RegionalStrategyRun,
)


# ── Blueprint v3.0 models ─────────────────────────────────────────────────
from app.models.data_freshness import DataFreshnessLog
from app.models.part_master import PartMaster
from app.models.guest import GuestSearchLog
from app.models.vendor_invite import VendorInviteToken
from app.models.report_snapshot_v2 import ReportSnapshotV2
from app.models.approval_chain import ApprovalChain

__all__ = [

    # Blueprint v3.0
    "DataFreshnessLog", "PartMaster", "GuestSearchLog",
    "VendorInviteToken", "ReportSnapshotV2", "ApprovalChain",

    # auth
    "User", "GuestSession", "VendorUser", "Organization", "OrganizationMembership",
    # bom
    "BOM", "BOMPart", "AnalysisResult", "BOMLineDependencyIndex",
    # project
    "Project", "ProjectACL", "ProjectEvent", "SearchSession", "SourcingCase",
    # vendor
    "Vendor", "VendorCapability", "VendorMatchRun", "VendorMatch",
    "VendorPerformanceSnapshot", "VendorImportBatch", "VendorImportRow",
    "VendorIdentityAlias", "VendorEvidenceAttachment",
    # rfq / quote / po / invoice / payment / gr / approval
    "RFQBatch", "RFQItem", "RFQVendorInvitation", "InvitationStatusEvent",
    "RFQQuoteHeader", "RFQQuoteLine", "PurchaseOrder", "POLineItem",
    "Invoice", "InvoiceLine", "Payment", "GoodsReceipt", "GoodsReceiptLine",
    "ApprovalRequest",
    # logistics
    "Shipment", "ShipmentMilestone",
    # chat
    "ChatThread", "ChatMessage",
    # market
    "FXRate", "FreightRate", "TariffScopeRegistry", "TariffSchedule", "CommodityIndex",
    "IntegrationRunLog", "SKUAvailabilitySnapshot", "HSMapping",
    "LaneRateBand", "CanonicalAvailabilitySnapshot",
    # pricing enrichment
    "PartToSkuMapping", "SKUOffer", "SKUOfferPriceBreak", "LaneScopeRegistry",
    "CanonicalSKU", "SourceSKULink", "CanonicalOfferSnapshot",
    # ops
    "BOMLineEvidenceCoverageFact", "EvidenceGapBacklogItem",
    "EnrichmentRunLog", "ConnectorHealthMetrics",
    # events / ops
    "PlatformEvent", "ReportSnapshot", "EventAuditLog", "IdempotencyRecord",
    # outcomes
    "QuoteOutcome", "OverrideEvent", "LeadTimeHistory", "VendorPerformance", "AnomalyFlag", "ConfidenceCalibrationData",
    # notifications
    "Notification", "NotificationPreference",
    # phase3 vendor intelligence
    "VendorLocation", "VendorExportCapability", "VendorLeadTimeBand",
    "VendorCommunicationScore", "VendorTrustTier",
    # phase3 matching
    "PartVendorIndex",
    # phase3 feedback / learning
    "RecommendationOverride", "LearningEvent",
    # phase3 market intelligence
    "CommodityPriceSignal", "VendorLeadTimeHistoryPhase3",
    "MarketAnomalyEvent", "RegionalStrategyRun",
]