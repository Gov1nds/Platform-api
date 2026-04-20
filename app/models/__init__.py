"""
Model registry — single import point for Alembic autogenerate and all
service-layer code.

Rules
-----
* Every ORM class must be imported here so that ``Base.metadata`` is fully
  populated when Alembic runs ``env.py``.
* ``Base.metadata.create_all()`` is NEVER called here (Alembic owns DDL).
* Import order follows dependency depth (least-dependent → most-dependent)
  to avoid circular-import surprises during module initialisation.
* The ``__all__`` list is the canonical public surface of the models layer;
  anything not listed here is considered internal.

Contract anchors
----------------
§2.1 – §2.93  Entity definitions
§7.2          Canonical column names and import paths
repo_c/app/models/ file map (Blueprint §3.3 file listing)
"""
from __future__ import annotations

# ── 0. Shared infrastructure ─────────────────────────────────────────────────
from app.models.base import Base, metadata_obj  # noqa: F401  (ensures metadata wired)
from app.models.enums import *  # noqa: F401, F403 — re-export all StrEnum vocabularies

# ── 1. Identity & Access (§2.2 – §2.5, §2.80 – §2.83) ───────────────────────
# VendorUser remains in user.py per the file map. Its vendor_id FK is named
# and use_alter=True so Alembic can safely resolve the user/vendor cycle.
from app.models.user import (
    Organization,
    OrganizationMembership,
    OAuthLink,
    RefreshToken,
    MFAEnrollment,
    User,
    VendorUser,
)

# ── 2. Part catalogue (§2.8) ──────────────────────────────────────────────────
from app.models.part_master import PartMaster

# ── 3. Workspace & Intake (§2.4 – §2.7, §2.91) ───────────────────────────────
from app.models.project import (
    Project,
    ProjectACL,
    WorkspaceDecision,
    BOMUpload,
    BOMLine,
)

# ── 4. Vendor Network (§2.13 – §2.21) ────────────────────────────────────────
from app.models.vendor import (
    Vendor,
    VendorPartCapability,
    VendorPerformanceSnapshot,
    Certification,
    VendorProfileClaim,
    VendorInvite,
    VendorTierTransition,
    VendorRating,
    PreferredVendorList,
    PreferredVendorMember,
)

# ── 5. Market Data (§2.22 – §2.26) ───────────────────────────────────────────
from app.models.market_data import (
    BaselinePrice,
    ForexRate,
    TariffRate,
    LogisticsRate,
    CommodityPriceTick,
)

# ── 6. Intelligence Orchestration (§2.9 – §2.12, §2.27 – §2.37) ─────────────
from app.models.intelligence import (
    NormalizationRun,
    NormalizationTrace,
    CandidateMatch,
    ReviewTask,
    VendorFilterResult,
    VendorScoreCache,
    ScoreBreakdown,
    StrategyRecommendation,
    SubstitutionRecommendation,
    ConsolidationInsight,
    DataSourcesSnapshot,
    EvidenceRecord,
)

# ── 7. Transactions (§2.38 – §2.54) ──────────────────────────────────────────
from app.models.transactions import (
    RFQ,
    RFQLine,
    RFQVendorInvite,
    Quote,
    QuoteLine,
    QuoteRevision,
    AwardDecision,
    ComparisonRun,
    PurchaseOrder,
    POLine,
    ChangeOrder,
)

# ── 8. Fulfilment (§2.55 – §2.67) ────────────────────────────────────────────
from app.models.fulfilment import (
    Shipment,
    ShipmentEvent,
    GoodsReceipt,
    GRLine,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentEvent,
)

# ── 9. Approval & Compliance Workflow (§2.68 – §2.69) ────────────────────────
from app.models.approval import (
    ApprovalRequest,
    ApprovalDecision,
    Dispute,
    ExceptionCase,
)

# ── 10. Chat & Negotiation (§2.60 – §2.64) ───────────────────────────────────
from app.models.chat import (
    ChatThread,
    ThreadParticipant,
    ChatMessage,
    OfferEvent,
    Document,
)

# ── 11. Notifications & Tasks (§2.65 – §2.69) ────────────────────────────────
from app.models.notification import (
    Notification,
    NotificationTemplate,
    NotificationPreference,
    OutboxMessage,
    Task,
    Alert,
)

# ── 12. Guest / Lead Generation (§2.70 – §2.73) ──────────────────────────────
from app.models.guest import (
    GuestSession,
    GuestSearchLog,
    GuestReportSnapshot,
    GuestRateLimitBucket,
)

# ── 13. Audit & Observability (§2.74 – §2.76) ────────────────────────────────
from app.models.audit import (
    EventAuditLog,
    DataFreshnessLog,
    IntegrationRunLog,
)

# ── 14. Analytics & Reporting (§2.84 – §2.88) ────────────────────────────────
from app.models.analytics import (
    ReportSchedule,
    ReportRun,
    InsightSummary,
    SpendSnapshot,
    SavingsSnapshot,
    CategoryInsight,
    RiskDashboardSnapshot,
    QuoteIntelligenceSnapshot,
    LeadTimeAnalysis,
    OperationalStatusView,
    SnapshotMetadata,
)

# ── 15. Configuration, Compliance & Flagged-JSON Promotions (§2.77–§2.79, §2.89–§2.93) ──
from app.models.config import (
    ConfigVersion,
    FeatureFlag,
    DataSubjectRequest,
    PIIRedactionRule,
    ExportControlFlag,
    NormalizationTraceMerge,
    ConsolidationInsightLine,
    DataSourcesSnapshotLink,
)


# ─────────────────────────────────────────────────────────────────────────────
# __all__ — the canonical public surface of the models layer
# ─────────────────────────────────────────────────────────────────────────────

__all__: list[str] = [
    # ── Infrastructure ────────────────────────────────────────────────────────
    "Base",
    "metadata_obj",

    # ── Identity & Access ─────────────────────────────────────────────────────
    "Organization",
    "OrganizationMembership",
    "OAuthLink",
    "RefreshToken",
    "MFAEnrollment",
    "User",
    "VendorUser",

    # ── Part catalogue ────────────────────────────────────────────────────────
    "PartMaster",

    # ── Workspace & Intake ────────────────────────────────────────────────────
    "Project",
    "ProjectACL",
    "WorkspaceDecision",
    "BOMUpload",
    "BOMLine",

    # ── Vendor Network ────────────────────────────────────────────────────────
    "Vendor",
    "VendorPartCapability",
    "VendorPerformanceSnapshot",
    "Certification",
    "VendorProfileClaim",
    "VendorInvite",
    "VendorTierTransition",
    "VendorRating",
    "PreferredVendorList",
    "PreferredVendorMember",

    # ── Market Data ───────────────────────────────────────────────────────────
    "BaselinePrice",
    "ForexRate",
    "TariffRate",
    "LogisticsRate",
    "CommodityPriceTick",

    # ── Intelligence Orchestration ────────────────────────────────────────────
    "NormalizationRun",
    "NormalizationTrace",
    "CandidateMatch",
    "ReviewTask",
    "VendorFilterResult",
    "VendorScoreCache",
    "ScoreBreakdown",
    "StrategyRecommendation",
    "SubstitutionRecommendation",
    "ConsolidationInsight",
    "DataSourcesSnapshot",
    "EvidenceRecord",

    # ── Transactions ──────────────────────────────────────────────────────────
    "RFQ",
    "RFQLine",
    "RFQVendorInvite",
    "Quote",
    "QuoteLine",
    "QuoteRevision",
    "AwardDecision",
    "ComparisonRun",
    "PurchaseOrder",
    "POLine",
    "ChangeOrder",

    # ── Fulfilment ────────────────────────────────────────────────────────────
    "Shipment",
    "ShipmentEvent",
    "GoodsReceipt",
    "GRLine",
    "Invoice",
    "InvoiceLine",
    "Payment",
    "PaymentEvent",

    # ── Approval & Compliance Workflow ────────────────────────────────────────
    "ApprovalRequest",
    "ApprovalDecision",
    "Dispute",
    "ExceptionCase",

    # ── Chat & Negotiation ────────────────────────────────────────────────────
    "ChatThread",
    "ThreadParticipant",
    "ChatMessage",
    "OfferEvent",
    "Document",

    # ── Notifications & Tasks ─────────────────────────────────────────────────
    "Notification",
    "NotificationTemplate",
    "NotificationPreference",
    "OutboxMessage",
    "Task",
    "Alert",

    # ── Guest / Lead Generation ───────────────────────────────────────────────
    "GuestSession",
    "GuestSearchLog",
    "GuestReportSnapshot",
    "GuestRateLimitBucket",

    # ── Audit & Observability ─────────────────────────────────────────────────
    "EventAuditLog",
    "DataFreshnessLog",
    "IntegrationRunLog",

    # ── Analytics & Reporting ─────────────────────────────────────────────────
    "ReportSchedule",
    "ReportRun",
    "InsightSummary",
    "SpendSnapshot",
    "SavingsSnapshot",
    "CategoryInsight",
    "RiskDashboardSnapshot",
    "QuoteIntelligenceSnapshot",
    "LeadTimeAnalysis",
    "OperationalStatusView",
    "SnapshotMetadata",

    # ── Configuration, Compliance & Join Tables ───────────────────────────────
    "ConfigVersion",
    "FeatureFlag",
    "DataSubjectRequest",
    "PIIRedactionRule",
    "ExportControlFlag",
    "NormalizationTraceMerge",
    "ConsolidationInsightLine",
    "DataSourcesSnapshotLink",
]
