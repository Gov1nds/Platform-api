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
    VendorPerformanceSnapshot,
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
    HSMapping, LaneRateBand, BOMLineDependencyIndex, EnrichmentRunLog,
)
from app.models.canonical import (
    CanonicalSKU, SourceSKULink, ConnectorHealthMetrics,
    CanonicalOfferSnapshot, CanonicalAvailabilitySnapshot,
)
from app.models.events import (
    PlatformEvent, ReportSnapshot, EventAuditLog, IdempotencyRecord,
)
from app.models.notification import Notification, NotificationPreference

__all__ = [
    # auth
    "User", "GuestSession", "VendorUser", "Organization", "OrganizationMembership",
    # bom
    "BOM", "BOMPart", "AnalysisResult", "BOMLineDependencyIndex",
    # project
    "Project", "ProjectACL", "ProjectEvent", "SearchSession", "SourcingCase",
    # vendor
    "Vendor", "VendorCapability", "VendorMatchRun", "VendorMatch",
    "VendorPerformanceSnapshot",
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
    "PartToSkuMapping", "SKUOffer", "SKUOfferPriceBreak",
    "CanonicalSKU", "SourceSKULink", "CanonicalOfferSnapshot",
    # ops
    "EnrichmentRunLog", "ConnectorHealthMetrics",
    # events / ops
    "PlatformEvent", "ReportSnapshot", "EventAuditLog", "IdempotencyRecord",
    # notifications
    "Notification", "NotificationPreference",
]
