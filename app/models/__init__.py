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

__all__ = [
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
]