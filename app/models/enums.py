"""
Single source of truth for every status / type / role vocabulary in the system.

Every ``StrEnum`` in this module corresponds to a numbered subsection in
contract §3 (Status Vocabularies) and drives:

1. SQLAlchemy CHECK constraints on the backing VARCHAR columns.
2. Pydantic v2 schema validation (schemas layer imports these directly).
3. Service-layer state-machine guards.

Design rules
------------
* Values are stored **exactly** as listed in the contract — case matters.
* No PostgreSQL ENUM TYPE is used: columns are VARCHAR + CHECK so that new
  values can be added via expand/contract migrations with zero downtime.
* Python ``StrEnum`` (3.11+) is used so ``enum_value == "RAW"`` comparisons
  work transparently without ``.value`` noise.
"""
from __future__ import annotations

from enum import StrEnum

# =============================================================================
# Core workflow state machines (SM-001 → SM-016)
# =============================================================================


class BOMLineStatus(StrEnum):
    """Contract §3.1 — SM-001. 17 states. Initial: RAW. Terminal: CLOSED, CANCELLED."""

    RAW = "RAW"
    NORMALIZING = "NORMALIZING"
    NORMALIZED = "NORMALIZED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ENRICHING = "ENRICHING"
    ENRICHED = "ENRICHED"
    SCORING = "SCORING"
    SCORED = "SCORED"
    RFQ_PENDING = "RFQ_PENDING"
    RFQ_SENT = "RFQ_SENT"
    QUOTED = "QUOTED"
    AWARDED = "AWARDED"
    ORDERED = "ORDERED"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class ProjectState(StrEnum):
    """Contract §3.2 — SM-002. 12 states. Initial: DRAFT."""

    DRAFT = "DRAFT"
    INTAKE_COMPLETE = "INTAKE_COMPLETE"
    ANALYSIS_IN_PROGRESS = "ANALYSIS_IN_PROGRESS"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    SOURCING_ACTIVE = "SOURCING_ACTIVE"
    ORDERING_IN_PROGRESS = "ORDERING_IN_PROGRESS"
    EXECUTION_ACTIVE = "EXECUTION_ACTIVE"
    PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
    FULLY_DELIVERED = "FULLY_DELIVERED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class ProjectSessionType(StrEnum):
    """Contract §3.3. One-way transition only (session → project)."""

    SESSION = "session"
    PROJECT = "project"


class BOMUploadImportStatus(StrEnum):
    """Contract §3.4."""

    RECEIVED = "RECEIVED"
    PARSING = "PARSING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class RFQStatus(StrEnum):
    """Contract §3.5 — SM-004."""

    DRAFT = "DRAFT"
    SENT = "SENT"
    PARTIALLY_RESPONDED = "PARTIALLY_RESPONDED"
    FULLY_RESPONDED = "FULLY_RESPONDED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class QuoteStatus(StrEnum):
    """Contract §3.6 — SM-005."""

    PENDING = "PENDING"
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    REVISED = "REVISED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


class PurchaseOrderStatus(StrEnum):
    """Contract §3.7 — SM-006. 15 states including ON_HOLD, CHANGE_ORDER_PENDING."""

    PO_APPROVED = "PO_APPROVED"
    PO_SENT = "PO_SENT"
    VENDOR_ACCEPTED = "VENDOR_ACCEPTED"
    PRODUCTION_STARTED = "PRODUCTION_STARTED"
    QUALITY_CHECK = "QUALITY_CHECK"
    PACKED = "PACKED"
    SHIPPED = "SHIPPED"
    CUSTOMS = "CUSTOMS"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    GR_CONFIRMED = "GR_CONFIRMED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"
    CHANGE_ORDER_PENDING = "CHANGE_ORDER_PENDING"


class ShipmentState(StrEnum):
    """Contract §3.8 — SM-007."""

    BOOKED = "BOOKED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    CUSTOMS_HOLD = "CUSTOMS_HOLD"
    CUSTOMS_CLEARED = "CUSTOMS_CLEARED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    RETURNED = "RETURNED"


class InvoiceState(StrEnum):
    """Contract §3.9 — SM-008."""

    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    APPROVED = "APPROVED"
    DISPUTED = "DISPUTED"
    DISPUTE_RESOLVED = "DISPUTE_RESOLVED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class VendorParticipation(StrEnum):
    """Contract §3.10 — SM-009."""

    GHOST = "GHOST"
    INVITED = "INVITED"
    CLAIM_PENDING = "CLAIM_PENDING"
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class VendorTier(StrEnum):
    """Contract §3.11."""

    GHOST = "GHOST"
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"


class VendorProfileClaimStatus(StrEnum):
    """Contract §3.12 — SM-010."""

    INITIATED = "INITIATED"
    EMAIL_VERIFIED = "EMAIL_VERIFIED"
    BUSINESS_VERIFIED = "BUSINESS_VERIFIED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CertificationStatus(StrEnum):
    """Contract §3.13."""

    UPLOADED = "UPLOADED"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class ApprovalRequestStatus(StrEnum):
    """Contract §3.14 — SM-011."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ChatThreadStatus(StrEnum):
    """Contract §3.15 — SM-012."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    ARCHIVED = "ARCHIVED"


class ReviewTaskStatus(StrEnum):
    """Contract §3.16 — SM-013."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    ABANDONED = "ABANDONED"


class FreshnessStatus(StrEnum):
    """Contract §3.17 — SM-014. Applies to baseline_price, forex_rate,
    tariff_rate, logistics_rate, evidence_record."""

    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    LOCKED = "LOCKED"


class OutboxMessageState(StrEnum):
    """Contract §3.18 — SM-015."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD_LETTERED = "DEAD_LETTERED"


class DataSubjectRequestState(StrEnum):
    """Contract §3.19 — SM-016."""

    RECEIVED = "RECEIVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class GuestSessionState(StrEnum):
    """Contract §3.20 — SM-003."""

    NEW = "NEW"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CONVERTED = "CONVERTED"


# =============================================================================
# Identity / access enums (§3.21 – §3.24)
# =============================================================================


class UserStatus(StrEnum):
    """Contract §3.21."""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"
    DELETED = "DELETED"


class VendorUserStatus(StrEnum):
    """Contract §3.22."""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class OrganizationBillingPlan(StrEnum):
    """Contract §3.23."""

    FREE = "FREE"
    STARTER = "STARTER"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


class UserRole(StrEnum):
    """Contract §3.24 — CN-2 canonical values. Aliases normalize before persistence."""

    OWNER = "owner"
    ADMIN = "admin"
    APPROVER = "approver"
    BUYER_EDITOR = "buyer_editor"
    BUYER_VIEWER = "buyer_viewer"
    VENDOR_REP = "vendor_rep"
    VENDOR_ADMIN = "vendor_admin"
    PGI_ADMIN = "pgi_admin"


class OrganizationMembershipRole(StrEnum):
    """Buyer-side organization membership roles.

    Vendor roles intentionally live only on VendorUser, not buyer org
    memberships.
    """

    OWNER = "owner"
    ADMIN = "admin"
    APPROVER = "approver"
    BUYER_EDITOR = "buyer_editor"
    BUYER_VIEWER = "buyer_viewer"
    PGI_ADMIN = "pgi_admin"


# =============================================================================
# Workflow / decision enums (§3.25 – §3.31)
# =============================================================================


class ChangeOrderStatus(StrEnum):
    """Contract §3.25."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DisputeStatus(StrEnum):
    """Contract §3.26."""

    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class ExceptionCaseStatus(StrEnum):
    """Contract §3.27."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class TaskStatus(StrEnum):
    """Contract §3.28."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class NotificationStatus(StrEnum):
    """Contract §3.29."""

    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    READ = "READ"


class NormalizationRunStatus(StrEnum):
    """Contract §3.30."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReportRunState(StrEnum):
    """Contract §3.31."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# =============================================================================
# Channel / source enums (§3.32 – §3.38)
# =============================================================================


class ShipmentEventSource(StrEnum):
    """Contract §3.32."""

    WEBHOOK = "webhook"
    POLLING = "polling"


class QuoteSourceChannel(StrEnum):
    """Contract §3.33."""

    PORTAL = "portal"
    EMAIL = "email"
    OCR = "ocr"


class RFQSendChannel(StrEnum):
    """Contract §3.34."""

    PORTAL = "portal"
    EMAIL = "email"
    API = "api"
    MULTI = "multi"


class RFQInviteDeliveryMethod(StrEnum):
    """Contract §3.35."""

    PORTAL = "portal"
    EMAIL = "email"
    API = "api"


class NotificationChannel(StrEnum):
    """Contract §3.36."""

    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    IN_APP = "in_app"


class BOMUploadSourceType(StrEnum):
    """Contract §3.37."""

    CSV = "csv"
    XLSX = "xlsx"
    TYPED = "typed"
    SINGLE_SEARCH = "single_search"


class IntegrationCircuitState(StrEnum):
    """Contract §3.38."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# =============================================================================
# Vendor classification / capability (§3.39 – §3.42)
# =============================================================================


class VendorType(StrEnum):
    """Contract §3.39."""

    MANUFACTURER = "manufacturer"
    DISTRIBUTOR = "distributor"
    CONTRACT_MANUFACTURER = "contract_manufacturer"
    BROKER = "broker"
    TRADING_COMPANY = "trading_company"


class VendorPlatformIntegrationLevel(StrEnum):
    """Contract §3.40."""

    API_CONNECTED = "api_connected"
    PORTAL_ONLY = "portal_only"
    EMAIL_ONLY = "email_only"


class VendorCapabilityConfidenceSource(StrEnum):
    """Contract §3.41."""

    HISTORICAL = "historical"
    DECLARED = "declared"
    INFERRED = "inferred"


class VendorScoreConfidence(StrEnum):
    """Contract §3.42."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class VendorTierTransitionTrigger(StrEnum):
    """Actor that triggered a vendor tier transition."""

    SYSTEM = "system"
    ADMIN = "admin"
    VENDOR = "vendor"


class ScoreDimension(StrEnum):
    """Vendor score-cache dimension names."""

    COST_COMPETITIVENESS = "cost_competitiveness"
    LEAD_TIME_AVAILABILITY = "lead_time_availability"
    QUALITY_RELIABILITY = "quality_reliability"
    STRATEGIC_FIT = "strategic_fit"
    OPERATIONAL_CAPABILITY = "operational_capability"


class SpendGrouping(StrEnum):
    """Analytics spend snapshot grouping names."""

    CATEGORY = "category"
    VENDOR = "vendor"
    PROJECT = "project"
    MONTH = "month"
    COUNTRY = "country"


class BulkActionKind(StrEnum):
    """BOM line bulk action request kinds."""

    TAG = "tag"
    SET_PRIORITY = "set_priority"
    EXCLUDE = "exclude"
    SEND_TO_RFQ = "send_to_rfq"


class VendorOrderUpdateType(StrEnum):
    """Vendor production update request kinds."""

    PRODUCTION_STARTED = "production_started"
    QUALITY_CHECK = "quality_check"
    PACKED = "packed"
    SHIPPED = "shipped"
    TEXT_UPDATE = "text_update"


# =============================================================================
# Fulfilment enums (§3.43 – §3.45)
# =============================================================================


class PaymentMethod(StrEnum):
    """Contract §3.43."""

    WIRE = "WIRE"
    ACH = "ACH"
    LC = "LC"
    OTHER = "OTHER"


class GRLineCondition(StrEnum):
    """Contract §3.44."""

    OK = "OK"
    DAMAGED = "DAMAGED"
    NCR = "NCR"


class InvoiceLineMatchStatus(StrEnum):
    """Contract §3.45."""

    MATCHED = "MATCHED"
    QTY_OUT_OF_TOLERANCE = "QTY_OUT_OF_TOLERANCE"
    PRICE_OUT_OF_TOLERANCE = "PRICE_OUT_OF_TOLERANCE"
    NO_MATCH = "NO_MATCH"


# =============================================================================
# Project profile / priority (§3.46 – §3.48)
# =============================================================================


class ProjectWeightProfile(StrEnum):
    """Contract §3.46."""

    SPEED_FIRST = "speed_first"
    COST_FIRST = "cost_first"
    QUALITY_FIRST = "quality_first"
    BALANCED = "balanced"
    CUSTOM = "custom"


class ProjectStage(StrEnum):
    """Contract §3.47."""

    PROTOTYPE = "prototype"
    PILOT = "pilot"
    PRODUCTION = "production"


class Priority(StrEnum):
    """Contract §3.48 — shared by Project, BOM_Line, Task."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


# =============================================================================
# Chat domain (§3.49 – §3.53)
# =============================================================================


class ChatThreadType(StrEnum):
    """Contract §3.49."""

    QUOTE = "quote"
    ORDER = "order"
    GENERAL = "general"


class ChatMessageType(StrEnum):
    """Contract §3.50."""

    TEXT = "text"
    FILE = "file"
    OFFER = "offer"
    STATUS_UPDATE = "status_update"
    SYSTEM = "system"


class ChatMessageSenderType(StrEnum):
    """Contract §3.51 — CN-8 adds ``system`` to the original buyer/vendor set."""

    BUYER = "buyer"
    VENDOR = "vendor"
    SYSTEM = "system"


class ChatMessageDeliveryStatus(StrEnum):
    """Contract §3.52."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class OfferEventType(StrEnum):
    """Contract §3.53."""

    PRICE = "price"
    LEAD_TIME = "lead_time"
    QUANTITY = "quantity"
    COMBINED = "combined"


# =============================================================================
# Document / market-data enums (§3.54 – §3.60)
# =============================================================================


class DocumentEntityType(StrEnum):
    """Contract §3.54."""

    RFQ = "rfq"
    QUOTE = "quote"
    PO = "po"
    SHIPMENT = "shipment"
    INVOICE = "invoice"
    CHAT = "chat"
    CERTIFICATION = "certification"


class DocumentVirusScanStatus(StrEnum):
    """Contract §3.55."""

    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"


class BaselinePriceSourceType(StrEnum):
    """Contract §3.56."""

    DISTRIBUTOR = "distributor"
    MARKET = "market"
    HISTORICAL = "historical"


class ForexRateSource(StrEnum):
    """Contract §3.58."""

    OPEN_EXCHANGE_RATES = "open_exchange_rates"
    XE_API = "xe_api"


class Carrier(StrEnum):
    """Contract §3.59 — CN-10 normalises both Shipment.carrier and
    Logistics_Rate.carrier to the same 5-value set (``custom`` rejected)."""

    DHL = "DHL"
    FEDEX = "FedEx"
    UPS = "UPS"
    MAERSK = "Maersk"
    OTHER = "other"


class CommodityExchange(StrEnum):
    """Contract §3.60."""

    LME = "LME"
    FASTMARKETS = "Fastmarkets"
    CME = "CME"


# =============================================================================
# Compliance / data-governance enums (§3.61 – §3.75)
# =============================================================================


class CertificationType(StrEnum):
    """Contract §3.61."""

    ISO_9001 = "ISO_9001"
    ISO_14001 = "ISO_14001"
    ISO_45001 = "ISO_45001"
    ROHS = "RoHS"
    REACH = "REACH"
    CE = "CE"
    IATF_16949 = "IATF_16949"
    AS9100 = "AS9100"
    CUSTOM = "custom"


class EvidenceDataPointType(StrEnum):
    """Contract §3.62."""

    PRICE = "price"
    LEAD_TIME = "lead_time"
    TARIFF = "tariff"
    FREIGHT = "freight"
    PERFORMANCE = "performance"
    CERTIFICATION = "certification"
    FOREX = "forex"


class FeatureFlagScope(StrEnum):
    """Contract §3.63."""

    GLOBAL = "global"
    ORGANIZATION = "organization"
    USER = "user"


class UserPrioritySensitivity(StrEnum):
    """Contract §3.64."""

    COST = "cost"
    SPEED = "speed"
    QUALITY = "quality"
    COMPLIANCE = "compliance"


class EventActorType(StrEnum):
    """Contract §3.65."""

    USER = "user"
    SYSTEM = "system"
    VENDOR = "vendor"
    ADMIN = "admin"


class FreshnessLogStatus(StrEnum):
    """Contract §3.66 (lowercase — not confused with SM-014 freshness states)."""

    SUCCESS = "success"
    ERROR = "error"
    STALE = "stale"


class ApprovalRequestEntityType(StrEnum):
    """Contract §3.67."""

    PURCHASE_ORDER = "purchase_order"
    INVOICE = "invoice"
    CHANGE_ORDER = "change_order"


class ApprovalDecisionValue(StrEnum):
    """Contract §3.68."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DisputeEntityType(StrEnum):
    """Contract §3.69."""

    INVOICE = "invoice"
    PO = "po"
    SHIPMENT = "shipment"


class TaskType(StrEnum):
    """Contract §3.70."""

    REVIEW_NORMALIZATION = "review_normalization"
    APPROVE_PO = "approve_po"
    CONFIRM_GR = "confirm_gr"
    RESPOND_TO_RFQ = "respond_to_rfq"
    OTHER = "other"


class VendorFilterEliminationStep(StrEnum):
    """Contract §3.71."""

    HARD_FILTER = "hard_filter"
    TECHNICAL_FIT_BELOW_THRESHOLD = "technical_fit_below_threshold"


class SourcingMode(StrEnum):
    """Contract §3.72 — shared by Strategy_Recommendation.recommended_mode
    and BOM_Line.sourcing_type."""

    LOCAL_DIRECT = "local_direct"
    INTERNATIONAL_DIRECT = "international_direct"
    DISTRIBUTOR = "distributor"
    BROKER = "broker"
    CONTRACT_MANUFACTURER = "contract_manufacturer"


class NormalizationDecisionType(StrEnum):
    """Contract §3.73."""

    AUTO = "auto"
    REVIEW_APPROVED = "review_approved"
    REVIEW_EDITED = "review_edited"
    MANUAL = "manual"


class PIIRedactionMethod(StrEnum):
    """Contract §3.74."""

    MASK = "mask"
    HASH = "hash"
    REMOVE = "remove"


class ExportControlClassification(StrEnum):
    """Contract §3.75."""

    ITAR = "ITAR"
    EAR = "EAR"
    NONE = "NONE"


# =============================================================================
# Risk / severity / exceptions (§3.76 – §3.78)
# =============================================================================


class RiskFlag(StrEnum):
    """Contract §3.76 — appears inside ``enrichment_json.risk_flags[].flag``."""

    SOLE_SOURCE = "SOLE_SOURCE"
    LONG_LEAD = "LONG_LEAD"
    HIGH_TARIFF_EXPOSURE = "HIGH_TARIFF_EXPOSURE"
    CURRENCY_VOLATILE = "CURRENCY_VOLATILE"
    GEOPOLITICAL_RISK = "GEOPOLITICAL_RISK"
    COMPLIANCE_GAP = "COMPLIANCE_GAP"


class ExceptionType(StrEnum):
    """Contract §3.77."""

    SLA_BREACH = "sla_breach"
    STALE_TRACKING = "stale_tracking"
    LOW_CONFIDENCE_OCR = "low_confidence_ocr"
    THREE_WAY_MISMATCH = "three_way_mismatch"
    OTHER = "other"


class Severity(StrEnum):
    """Contract §3.78 — shared by Exception_Case, Alert, enrichment risk flags."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# =============================================================================
# Config / compliance (§3.80 – §3.83)
# =============================================================================


class ConfigType(StrEnum):
    """Contract §3.80."""

    NLP_MODEL = "nlp_model"
    SCORING_MODEL = "scoring_model"
    WEIGHT_PROFILE_DEFAULTS = "weight_profile_defaults"
    APPROVAL_THRESHOLDS = "approval_thresholds"


class DataSubjectRequestType(StrEnum):
    """Contract §3.81."""

    ACCESS = "access"
    RECTIFY = "rectify"
    ERASE = "erase"
    PORTABILITY = "portability"


class OAuthProvider(StrEnum):
    """Contract §3.82."""

    GOOGLE = "google"
    LINKEDIN = "linkedin"
    MICROSOFT = "microsoft"
    SAML = "saml"


class MFAMethod(StrEnum):
    """Contract §3.83."""

    TOTP = "totp"
    SMS = "sms"
    WEBAUTHN = "webauthn"


class ReportCadence(StrEnum):
    """Contract §3.84."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


class PaymentEventSource(StrEnum):
    """Contract §3.85."""

    ERP = "erp"
    MANUAL = "manual"
    GATEWAY_WEBHOOK = "gateway_webhook"


class GuestRateLimitScope(StrEnum):
    """Contract §3.86."""

    IP = "ip"
    SESSION = "session"


class ProjectACLRole(StrEnum):
    """Contract §3.87."""

    OWNER = "owner"
    VIEWER = "viewer"
    APPROVER = "approver"
    EDITOR = "editor"


class VendorUserRole(StrEnum):
    """Contract §3.88."""

    VENDOR_REP = "vendor_rep"
    VENDOR_ADMIN = "vendor_admin"


class WorkspaceDecisionStateValue(StrEnum):
    """Contract §3.89 — same as ProjectSessionType but declared separately
    so CHECK constraint references the canonical enum for this table."""

    SESSION = "session"
    PROJECT = "project"


class ThreadParticipantType(StrEnum):
    """Contract §3.90."""

    BUYER = "buyer"
    VENDOR = "vendor"


# =============================================================================
# Data-sources-snapshot link — source type discriminator (CN-17)
# =============================================================================


class DataSourceLinkType(StrEnum):
    """Values accepted in ``data_sources_snapshot_link.source_type`` (CN-17).

    The link table replaces the five UUID[] columns on ``data_sources_snapshot``.
    Each value corresponds to one of the five previously-flagged arrays.
    """

    BASELINE_PRICE = "baseline_price"
    FOREX_RATE = "forex_rate"
    TARIFF_RATE = "tariff_rate"
    LOGISTICS_RATE = "logistics_rate"
    VENDOR_PERFORMANCE_SNAPSHOT = "vendor_performance_snapshot"


# =============================================================================
# Helpers — turn a StrEnum class into a tuple[str, ...] suitable for CHECK.
# =============================================================================


def values_of(enum_cls: type[StrEnum]) -> tuple[str, ...]:
    """Return a tuple of string values for an enum class, in declaration order."""
    return tuple(m.value for m in enum_cls)


__all__ = [
    "BOMLineStatus",
    "ProjectState",
    "ProjectSessionType",
    "BOMUploadImportStatus",
    "RFQStatus",
    "QuoteStatus",
    "PurchaseOrderStatus",
    "ShipmentState",
    "InvoiceState",
    "VendorParticipation",
    "VendorTier",
    "VendorProfileClaimStatus",
    "CertificationStatus",
    "ApprovalRequestStatus",
    "ChatThreadStatus",
    "ReviewTaskStatus",
    "FreshnessStatus",
    "OutboxMessageState",
    "DataSubjectRequestState",
    "GuestSessionState",
    "UserStatus",
    "VendorUserStatus",
    "OrganizationBillingPlan",
    "UserRole",
    "OrganizationMembershipRole",
    "ChangeOrderStatus",
    "DisputeStatus",
    "ExceptionCaseStatus",
    "TaskStatus",
    "NotificationStatus",
    "NormalizationRunStatus",
    "ReportRunState",
    "ShipmentEventSource",
    "QuoteSourceChannel",
    "RFQSendChannel",
    "RFQInviteDeliveryMethod",
    "NotificationChannel",
    "BOMUploadSourceType",
    "IntegrationCircuitState",
    "VendorType",
    "VendorPlatformIntegrationLevel",
    "VendorCapabilityConfidenceSource",
    "VendorScoreConfidence",
    "VendorTierTransitionTrigger",
    "ScoreDimension",
    "SpendGrouping",
    "BulkActionKind",
    "VendorOrderUpdateType",
    "PaymentMethod",
    "GRLineCondition",
    "InvoiceLineMatchStatus",
    "ProjectWeightProfile",
    "ProjectStage",
    "Priority",
    "ChatThreadType",
    "ChatMessageType",
    "ChatMessageSenderType",
    "ChatMessageDeliveryStatus",
    "OfferEventType",
    "DocumentEntityType",
    "DocumentVirusScanStatus",
    "BaselinePriceSourceType",
    "ForexRateSource",
    "Carrier",
    "CommodityExchange",
    "CertificationType",
    "EvidenceDataPointType",
    "FeatureFlagScope",
    "UserPrioritySensitivity",
    "EventActorType",
    "FreshnessLogStatus",
    "ApprovalRequestEntityType",
    "ApprovalDecisionValue",
    "DisputeEntityType",
    "TaskType",
    "VendorFilterEliminationStep",
    "SourcingMode",
    "NormalizationDecisionType",
    "PIIRedactionMethod",
    "ExportControlClassification",
    "RiskFlag",
    "ExceptionType",
    "Severity",
    "ConfigType",
    "DataSubjectRequestType",
    "OAuthProvider",
    "MFAMethod",
    "ReportCadence",
    "PaymentEventSource",
    "GuestRateLimitScope",
    "ProjectACLRole",
    "VendorUserRole",
    "WorkspaceDecisionStateValue",
    "ThreadParticipantType",
    "DataSourceLinkType",
    "values_of",
]
