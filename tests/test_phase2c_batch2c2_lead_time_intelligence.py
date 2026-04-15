from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.bom import BOM, BOMPart
from app.models.outcomes import LeadTimeHistory
from app.models.vendor import Vendor
from app.services.lead_time_intelligence_service import lead_time_intelligence_service
from app.services.outcome_data_service import outcome_data_service



def _make_bom(db_session, test_org):
    row = BOM(
        organization_id=test_org.id,
        source_file_name="phase2c-batch2c2.csv",
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
        quantity=Decimal("5"),
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



def test_lead_time_history_calculation_and_validation(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    completed = outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("10.00"),
        quoted_lead_time=Decimal("8"),
        quote_date=date(2026, 4, 1),
        order_date=date(2026, 4, 2),
        delivery_date=date(2026, 4, 12),
    )
    invalid = outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("10.50"),
        quoted_lead_time=Decimal("7"),
        quote_date=date(2026, 4, 3),
        order_date=date(2026, 4, 10),
        delivery_date=date(2026, 4, 8),
    )

    rows = lead_time_intelligence_service.sync_lead_time_history(
        db_session,
        quote_outcome_ids=[completed.id, invalid.id],
    )

    assert len(rows) == 1
    assert rows[0].quote_outcome_id == completed.id
    assert rows[0].actual_lead_time == Decimal("10")
    assert rows[0].lead_time_diff_days == Decimal("2")

    persisted = db_session.query(LeadTimeHistory).all()
    assert len(persisted) == 1



def test_vendor_performance_computes_on_time_rate_and_variance(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("10.00"),
        quoted_lead_time=Decimal("8"),
        is_accepted=True,
        quote_date=date(2026, 4, 1),
        order_date=date(2026, 4, 2),
        delivery_date=date(2026, 4, 12),
    )
    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("12.00"),
        quoted_lead_time=Decimal("9"),
        is_accepted=True,
        quote_date=date(2026, 4, 5),
        order_date=date(2026, 4, 6),
        delivery_date=date(2026, 4, 15),
    )
    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("11.00"),
        quoted_lead_time=Decimal("10"),
        is_accepted=False,
        quote_date=date(2026, 4, 9),
        order_date=date(2026, 4, 10),
        delivery_date=date(2026, 4, 19),
    )

    outcome_data_service.rebuild_vendor_performance(
        db_session,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    perf = lead_time_intelligence_service.get_vendor_performance(db_session, vendor_id=vendor.id)
    assert perf is not None
    assert perf.on_time_rate == Decimal("0.666667")
    assert perf.avg_lead_time == Decimal("9.33")
    assert perf.lead_time_variance == Decimal("1.5556")
    assert perf.po_win_rate == Decimal("0.666667")
    assert perf.price_variance == Decimal("0E-27")



def test_adjusted_lead_time_applies_average_vendor_delay(db_session, test_org):
    bom = _make_bom(db_session, test_org)
    part = _make_part(db_session, test_org, bom)
    vendor = _make_vendor(db_session)

    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("9.00"),
        quoted_lead_time=Decimal("10"),
        quote_date=date(2026, 4, 1),
        order_date=date(2026, 4, 2),
        delivery_date=date(2026, 4, 14),
    )
    outcome_data_service.ingest_quote_outcome(
        db_session,
        bom_line_id=part.id,
        vendor_id=vendor.id,
        quoted_price=Decimal("9.25"),
        quoted_lead_time=Decimal("10"),
        quote_date=date(2026, 4, 8),
        order_date=date(2026, 4, 9),
        delivery_date=date(2026, 4, 18),
    )
    outcome_data_service.sync_lead_time_history(db_session, vendor_ids=[vendor.id])

    adjusted = lead_time_intelligence_service.get_adjusted_lead_time(
        db_session,
        vendor_id=vendor.id,
        bom_line_id=part.id,
    )

    assert adjusted == Decimal("10.50")