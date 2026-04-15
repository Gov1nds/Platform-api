from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.outcomes import LeadTimeHistory, VendorPerformance
from app.services.outcome_data_service import outcome_data_service


class LeadTimeIntelligenceService:
    """Thin service facade for Phase 2C.2 lead-time history and vendor intelligence."""

    def sync_lead_time_history(
        self,
        db: Session,
        *,
        vendor_ids: Iterable[str] | None = None,
        quote_outcome_ids: Iterable[str] | None = None,
    ) -> list[LeadTimeHistory]:
        return outcome_data_service.sync_lead_time_history(
            db,
            vendor_ids=vendor_ids,
            quote_outcome_ids=quote_outcome_ids,
        )

    def get_vendor_performance(self, db: Session, *, vendor_id: str) -> VendorPerformance | None:
        return outcome_data_service.get_vendor_performance(db, vendor_id=vendor_id)

    def get_adjusted_lead_time(
        self,
        db: Session,
        *,
        vendor_id: str,
        bom_line_id: str,
    ) -> Decimal | None:
        return outcome_data_service.get_adjusted_lead_time(
            db,
            vendor_id=vendor_id,
            bom_line_id=bom_line_id,
        )


lead_time_intelligence_service = LeadTimeIntelligenceService()