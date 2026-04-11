"""
Canonical enums for the PGI Platform.

All state machine values, actor types, roles, and classification enums
are defined here as the single source of truth. Every model, schema,
and service MUST import from this module — never use raw string literals.

References: state-machines.md (SM-001 … SM-008), canonical-domain-model.md,
            roles-permissions.yaml (ACL-001, ACL-002)
"""
from __future__ import annotations

from enum import StrEnum


# ── BOM Upload ───────────────────────────────────────────────────────────────

class BOMUploadStatus(StrEnum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    AWAITING_MAPPING_CONFIRM = "AWAITING_MAPPING_CONFIRM"
    MAPPING_CONFIRMED = "MAPPING_CONFIRMED"
    INGESTED = "INGESTED"
    PARSE_FAILED = "PARSE_FAILED"


# ── SM-001: BOM Line Lifecycle ───────────────────────────────────────────────

class BOMLineStatus(StrEnum):
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


# ── SM-002: Project Lifecycle ────────────────────────────────────────────────

class ProjectStatus(StrEnum):
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


# ── SM-003: Search / Sourcing Session ────────────────────────────────────────

class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RFQ_SENT = "RFQ_SENT"
    QUOTED = "QUOTED"
    ORDERED = "ORDERED"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"
    PROMOTED_TO_PROJECT = "PROMOTED_TO_PROJECT"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# ── SM-004: RFQ Lifecycle ────────────────────────────────────────────────────

class RFQStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    PARTIALLY_RESPONDED = "PARTIALLY_RESPONDED"
    FULLY_RESPONDED = "FULLY_RESPONDED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# ── SM-005: Quote Lifecycle ──────────────────────────────────────────────────

class QuoteStatus(StrEnum):
    PENDING = "PENDING"
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    REVISED = "REVISED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


# ── SM-006: Purchase Order Lifecycle ─────────────────────────────────────────

class POStatus(StrEnum):
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


# ── SM-007: Shipment Lifecycle ───────────────────────────────────────────────

class ShipmentStatus(StrEnum):
    BOOKED = "BOOKED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    CUSTOMS_HOLD = "CUSTOMS_HOLD"
    CUSTOMS_CLEARED = "CUSTOMS_CLEARED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    RETURNED = "RETURNED"


# ── SM-008: Invoice Lifecycle ────────────────────────────────────────────────

class InvoiceStatus(StrEnum):
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


# ── MKT-002: Vendor Profile Lifecycle ────────────────────────────────────────

class VendorStatus(StrEnum):
    GHOST = "GHOST"
    INVITED = "INVITED"
    CLAIM_PENDING = "CLAIM_PENDING"
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


# ── Invitation Status ────────────────────────────────────────────────────────

class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    OPENED = "OPENED"
    DECLINED = "DECLINED"
    QUOTE_SUBMITTED = "QUOTE_SUBMITTED"
    EXPIRED = "EXPIRED"
    ACCEPTED = "ACCEPTED"
    AWARDED = "AWARDED"


# ── Approval Status ─────────────────────────────────────────────────────────

class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    WITHDRAWN = "WITHDRAWN"


# ── Guest Session Status ─────────────────────────────────────────────────────

class GuestSessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONVERTED = "CONVERTED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"


# ── Payment Status ───────────────────────────────────────────────────────────

class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    INITIATED = "INITIATED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


# ── Freshness Status ─────────────────────────────────────────────────────────

class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


# ── Actor Type ───────────────────────────────────────────────────────────────

class ActorType(StrEnum):
    USER = "USER"
    VENDOR = "VENDOR"
    SYSTEM = "SYSTEM"
    CRON = "CRON"
    ADMIN = "ADMIN"


# ── Role Hierarchies (ACL-001) ───────────────────────────────────────────────

class BuyerRole(StrEnum):
    GUEST = "GUEST"
    BUYER_VIEWER = "BUYER_VIEWER"
    BUYER_EDITOR = "BUYER_EDITOR"
    BUYER_APPROVER = "BUYER_APPROVER"
    BUYER_ADMIN = "BUYER_ADMIN"
    ORGANIZATION_OWNER = "ORGANIZATION_OWNER"


class VendorRole(StrEnum):
    VENDOR_REP = "VENDOR_REP"
    VENDOR_ADMIN = "VENDOR_ADMIN"


class PlatformRole(StrEnum):
    PGI_SUPPORT_ADMIN = "PGI_SUPPORT_ADMIN"
    PGI_COMPLIANCE_ADMIN = "PGI_COMPLIANCE_ADMIN"
    PGI_DATA_ADMIN = "PGI_DATA_ADMIN"
    PGI_ADMIN = "PGI_ADMIN"


# ── Role hierarchy ordering (for require_role enforcement) ───────────────────

BUYER_ROLE_HIERARCHY: list[str] = [
    BuyerRole.GUEST,
    BuyerRole.BUYER_VIEWER,
    BuyerRole.BUYER_EDITOR,
    BuyerRole.BUYER_APPROVER,
    BuyerRole.BUYER_ADMIN,
    BuyerRole.ORGANIZATION_OWNER,
]

VENDOR_ROLE_HIERARCHY: list[str] = [
    VendorRole.VENDOR_REP,
    VendorRole.VENDOR_ADMIN,
]

PLATFORM_ROLE_HIERARCHY: list[str] = [
    PlatformRole.PGI_SUPPORT_ADMIN,
    PlatformRole.PGI_COMPLIANCE_ADMIN,
    PlatformRole.PGI_DATA_ADMIN,
    PlatformRole.PGI_ADMIN,
]