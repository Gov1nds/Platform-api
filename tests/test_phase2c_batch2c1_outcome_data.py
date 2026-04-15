from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.bom import BOM, BOMPart
from app.models.outcomes import LeadTimeHistory, OverrideEvent, QuoteOutcome, VendorPerformance
from app.models.vendor import Vendor
from app.services.outcome_data_service import outcome_data_service



def _make_bom(db_session, test_org):
    row = BOM(
        organization_id=test_org.id,
        source_file_name="phase2c.csv",
        status="INGESTED",
    )
    db_session.add(row)
    db_session.flush()
    return row



def _make_part(db_session, test_org, bom, row_number: int = 1):
    row = BOMPart(
        bom_id=bom.id,
        organization_id=test_org.id,
        status="SCORED",
        row_number=row_number,
        item_id=f"ITEM-{row_number}",
        description=f"Part {row_number}",
        quantity=Decimal("10"),
    )
    db_session.add(row)
    db_session.flush()
    return row



def _make_vendor(db_session, name: str = "Vendor A"):
    row = Vendor(
        name=name,
        status="BASIC",
        country="US",
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row



def test_ingest_quote_outcome_is_append_only_and_validates_refs(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    first = outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("12.50"),
        quoted_lead_time=Decimal("14"),
        quote_date=date(2026, 4, 10),
    )
    second = outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("11.75"),
        quoted_lead_time=Decimal("12"),
        is_accepted=True,
        accepted_price=Decimal("11.70"),
        accepted_lead_time=Decimal("11"),
        quote_date=date(2026, 4, 11),
        order_date=date(2026, 4, 12),
        delivery_date=date(2026, 4, 20),
    )

    assert first.id != second.id
    persisted = db_session.query(QuoteOutcome).order_by(QuoteOutcome.created_at.asc()).all()
    assert len(persisted) == 2
    assert persisted[1].is_accepted is True

    with pytest.raises(ValueError):
        outcome_data_service.ingest_quote_outcome(
            db_session,
            bom_line_id="missing-line",
            vendor_id=vendor.id,
        )



def test_override_event_capture_is_append_only(db_session, test_org, test_user):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    recommended = _make_vendor(db_session, "Vendor Recommended")
    chosen = _make_vendor(db_session, "Vendor Chosen")

    row = outcome_data_service.log_override_event(
        db_session,
        event_id="override-001",
        user_id=test_user.id,
        bom_line_id=part.id,
        recommended_vendor_id=recommended.id,
        chosen_vendor_id=chosen.id,
        override_reason_code="relationship_preference",
        timestamp=datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc),
    )

    assert row.event_id == "override-001"
    persisted = db_session.query(OverrideEvent).all()
    assert len(persisted) == 1
    assert persisted[0].chosen_vendor_id == chosen.id



def test_vendor_performance_aggregation_computes_batch2c2_metrics(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor_a = _make_vendor(db_session, "Vendor A")
    vendor_b = _make_vendor(db_session, "Vendor B")

    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor_a.id,
        quoted_price=Decimal("10.00"),
        quoted_lead_time=Decimal("8"),
        is_accepted=True,
        accepted_price=Decimal("9.50"),
        accepted_lead_time=Decimal("7"),
        quote_date=date(2026, 4, 1),
        order_date=date(2026, 4, 2),
        delivery_date=date(2026, 4, 8),
    )
    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor_a.id,
        quoted_price=Decimal("10.50"),
        quoted_lead_time=Decimal("9"),
        is_accepted=False,
        quote_date=date(2026, 4, 5),
    )
    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor_b.id,
        quoted_price=Decimal("12.00"),
        quoted_lead_time=Decimal("15"),
        is_accepted=False,
        quote_date=date(2026, 4, 4),
    )

    rows = outcome_data_service.rebuild_vendor_performance(
        db_session,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    assert len(rows) == 2
    vendor_a_perf = next(row for row in rows if row.vendor_id == vendor_a.id)
    assert vendor_a_perf.po_win_rate == Decimal("0.5")
    assert vendor_a_perf.avg_lead_time == Decimal("6")
    assert vendor_a_perf.on_time_rate == Decimal("1")
    assert vendor_a_perf.lead_time_variance == Decimal("0.0000")
    assert vendor_a_perf.price_variance == Decimal("0E-8")

    vendor_b_perf = next(row for row in rows if row.vendor_id == vendor_b.id)
    assert vendor_b_perf.po_win_rate == Decimal("0")
    assert vendor_b_perf.avg_lead_time is None
    assert vendor_b_perf.on_time_rate is None
    assert vendor_b_perf.lead_time_variance is None

    persisted_histories = db_session.query(LeadTimeHistory).all()
    assert len(persisted_histories) == 1

    persisted = db_session.query(VendorPerformance).all()
    assert len(persisted) == 2