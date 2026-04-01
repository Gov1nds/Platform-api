"""Spend analytics service — ledger, rollups, trends, schedules."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from app.models.analytics import (
    SpendLedger,
    CategorySpendRollup,
    VendorSpendRollup,
    MonthlySpendSnapshot,
    SavingsRealized,
    DeliveryPerformanceRollup,
    ReportSchedule,
)
from app.models.project import Project
from app.models.rfq import RFQBatch
from app.models.vendor import Vendor
from app.models.tracking import PurchaseOrder, Shipment, Invoice, GoodsReceipt, PaymentState
from app.models.analysis import AnalysisResult

logger = logging.getLogger("analytics_service")


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _first_of_month(dt: Optional[datetime]) -> datetime:
    dt = dt or datetime.utcnow()
    return datetime(dt.year, dt.month, 1, tzinfo=dt.tzinfo)


def _month_key(dt: Optional[datetime]) -> str:
    dt = dt or datetime.utcnow()
    return f"{dt.year:04d}-{dt.month:02d}"


def _project_category_weights(project: Optional[Project]) -> Dict[str, float]:
    if not project:
        return {"uncategorized": 1.0}

    report = project.analyzer_report or {}
    parts = report.get("section_2_component_breakdown", []) or []
    weights = defaultdict(float)

    for item in parts:
        cat = (item.get("category") or "uncategorized").strip().lower()
        best_cost = _safe_float(item.get("best_cost"), None)
        if best_cost is None:
            best_cost = _safe_float(item.get("estimated_cost"), None)
        if best_cost is None:
            best_cost = _safe_float(item.get("cost"), 0.0)
        weights[cat] += max(best_cost, 0.0)

    total = sum(weights.values())
    if total <= 0:
        return {"uncategorized": 1.0}

    return {k: v / total for k, v in weights.items()}


def _allocate_amounts(total_amount: float, weights: Dict[str, float]) -> List[Tuple[str, float]]:
    if not weights:
        return [("uncategorized", round(total_amount, 6))]
    items = list(weights.items())
    amounts = []
    running = 0.0
    for idx, (cat, wt) in enumerate(items):
        if idx == len(items) - 1:
            amt = round(total_amount - running, 6)
        else:
            amt = round(total_amount * wt, 6)
            running += amt
        amounts.append((cat, amt))
    return amounts


def _project_context_from_rfq(db: Session, rfq_id: str) -> Tuple[Optional[Project], Optional[RFQBatch]]:
    rfq = db.query(RFQBatch).filter(RFQBatch.id == rfq_id).first()
    if not rfq:
        return None, None
    project = None
    if rfq.bom_id:
        project = db.query(Project).filter(Project.bom_id == rfq.bom_id).first()
    return project, rfq


def _vendor_name(db: Session, vendor_id: Optional[str]) -> Optional[str]:
    if not vendor_id:
        return None
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    return v.name if v else None


def _upsert_ledger_row(
    db: Session,
    *,
    project_id: Optional[str],
    rfq_id: Optional[str],
    vendor_id: Optional[str],
    purchase_order_id: Optional[str],
    shipment_id: Optional[str],
    invoice_id: Optional[str],
    ledger_type: str,
    source_type: str,
    source_id: str,
    category: str,
    region: Optional[str],
    currency: str,
    quantity: Optional[float],
    unit_price: Optional[float],
    amount: float,
    baseline_amount: Optional[float],
    realized_savings: Optional[float],
    occurred_at: Optional[datetime],
    metadata: Optional[Dict[str, Any]],
):
    existing = (
        db.query(SpendLedger)
        .filter(
            SpendLedger.source_type == source_type,
            SpendLedger.source_id == source_id,
            SpendLedger.ledger_type == ledger_type,
            SpendLedger.category == category,
        )
        .first()
    )
    if existing:
        existing.project_id = project_id
        existing.rfq_id = rfq_id
        existing.vendor_id = vendor_id
        existing.purchase_order_id = purchase_order_id
        existing.shipment_id = shipment_id
        existing.invoice_id = invoice_id
        existing.region = region
        existing.currency = currency or existing.currency or "USD"
        existing.quantity = quantity
        existing.unit_price = unit_price
        existing.amount = amount
        existing.baseline_amount = baseline_amount
        existing.realized_savings = realized_savings
        existing.occurred_at = occurred_at or existing.occurred_at or datetime.utcnow()
        existing.metadata_ = metadata or existing.metadata_ or {}
        existing.updated_at = datetime.utcnow()
        return existing

    row = SpendLedger(
        project_id=project_id,
        rfq_id=rfq_id,
        vendor_id=vendor_id,
        purchase_order_id=purchase_order_id,
        shipment_id=shipment_id,
        invoice_id=invoice_id,
        ledger_type=ledger_type,
        source_type=source_type,
        source_id=source_id,
        category=category or "uncategorized",
        region=region,
        currency=currency or "USD",
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        baseline_amount=baseline_amount,
        realized_savings=realized_savings,
        occurred_at=occurred_at or datetime.utcnow(),
        metadata_=metadata or {},
    )
    db.add(row)
    return row


def record_purchase_order_spend(
    db: Session,
    po: PurchaseOrder,
    project: Optional[Project] = None,
    rfq: Optional[RFQBatch] = None,
    actor: Optional[Any] = None,
):
    project = project or (db.query(Project).filter(Project.id == po.project_id).first() if po.project_id else None)
    rfq = rfq or (db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po.rfq_id else None)

    vendor = db.query(Vendor).filter(Vendor.id == po.vendor_id).first() if po.vendor_id else None
    category_weights = _project_category_weights(project)
    total_amount = _safe_float(po.total_amount, None)
    if total_amount is None:
        total_amount = _safe_float(po.subtotal, None)
    if total_amount is None and rfq:
        total_amount = _safe_float(rfq.total_estimated_cost, 0.0)

    baseline = _safe_float(rfq.total_estimated_cost, None) if rfq else None
    savings = (baseline - total_amount) if baseline is not None else None
    if project and total_amount is not None:
        for cat, amt in _allocate_amounts(total_amount, category_weights):
            _upsert_ledger_row(
                db,
                project_id=project.id,
                rfq_id=rfq.id if rfq else None,
                vendor_id=vendor.id if vendor else po.vendor_id,
                purchase_order_id=po.id,
                shipment_id=None,
                invoice_id=None,
                ledger_type="committed",
                source_type="purchase_order",
                source_id=str(po.id),
                category=cat,
                region=vendor.region if vendor else None,
                currency=po.currency or "USD",
                quantity=None,
                unit_price=None,
                amount=amt,
                baseline_amount=baseline,
                realized_savings=savings if savings is not None else None,
                occurred_at=po.issued_at or datetime.utcnow(),
                metadata={
                    "po_number": po.po_number,
                    "vendor_confirmation_status": po.vendor_confirmation_status,
                    "actor": getattr(actor, "id", None) if actor else None,
                },
            )
    if baseline is not None:
        _upsert_savings_row(
            db,
            project=project,
            rfq=rfq,
            vendor=vendor,
            po=po,
            invoice=None,
            source_type="purchase_order",
            source_id=str(po.id),
            currency=po.currency or "USD",
            baseline_amount=baseline,
            actual_amount=total_amount,
            realized_at=po.issued_at or datetime.utcnow(),
            notes="PO commitment recorded",
        )
    refresh_project_rollups(db, project.id if project else None)
    return total_amount


def record_invoice_spend(
    db: Session,
    invoice: Invoice,
    po: Optional[PurchaseOrder] = None,
    project: Optional[Project] = None,
    rfq: Optional[RFQBatch] = None,
    actor: Optional[Any] = None,
):
    po = po or db.query(PurchaseOrder).filter(PurchaseOrder.id == invoice.purchase_order_id).first()
    project = project or (db.query(Project).filter(Project.id == po.project_id).first() if po and po.project_id else None)
    rfq = rfq or (db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po and po.rfq_id else None)
    vendor = db.query(Vendor).filter(Vendor.id == invoice.vendor_id).first() if invoice.vendor_id else (db.query(Vendor).filter(Vendor.id == po.vendor_id).first() if po and po.vendor_id else None)

    total_amount = _safe_float(invoice.total_amount, None)
    if total_amount is None:
        total_amount = _safe_float(invoice.subtotal, 0.0) + _safe_float(invoice.taxes, 0.0)

    baseline = _safe_float(rfq.total_estimated_cost, None) if rfq else _safe_float(po.total_amount, None) if po else None
    savings = (baseline - total_amount) if baseline is not None else None

    if project:
        for cat, amt in _allocate_amounts(total_amount, _project_category_weights(project)):
            _upsert_ledger_row(
                db,
                project_id=project.id,
                rfq_id=rfq.id if rfq else None,
                vendor_id=vendor.id if vendor else None,
                purchase_order_id=po.id if po else None,
                shipment_id=None,
                invoice_id=invoice.id,
                ledger_type="invoiced",
                source_type="invoice",
                source_id=str(invoice.id),
                category=cat,
                region=vendor.region if vendor else None,
                currency=invoice.currency or "USD",
                quantity=None,
                unit_price=None,
                amount=amt,
                baseline_amount=baseline,
                realized_savings=savings if savings is not None else None,
                occurred_at=invoice.invoice_date or datetime.utcnow(),
                metadata={
                    "invoice_number": invoice.invoice_number,
                    "invoice_status": invoice.invoice_status,
                    "actor": getattr(actor, "id", None) if actor else None,
                },
            )

    _upsert_savings_row(
        db,
        project=project,
        rfq=rfq,
        vendor=vendor,
        po=po,
        invoice=invoice,
        source_type="invoice",
        source_id=str(invoice.id),
        currency=invoice.currency or "USD",
        baseline_amount=baseline,
        actual_amount=total_amount,
        realized_at=invoice.matched_at or invoice.invoice_date or datetime.utcnow(),
        notes="Invoice recorded",
    )
    refresh_project_rollups(db, project.id if project else None)
    return total_amount


def record_payment_spend(
    db: Session,
    payment: PaymentState,
    invoice: Invoice,
    po: Optional[PurchaseOrder] = None,
    project: Optional[Project] = None,
    rfq: Optional[RFQBatch] = None,
    actor: Optional[Any] = None,
):
    po = po or db.query(PurchaseOrder).filter(PurchaseOrder.id == payment.purchase_order_id).first()
    project = project or (db.query(Project).filter(Project.id == po.project_id).first() if po and po.project_id else None)
    rfq = rfq or (db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po and po.rfq_id else None)
    vendor = db.query(Vendor).filter(Vendor.id == invoice.vendor_id).first() if invoice.vendor_id else (db.query(Vendor).filter(Vendor.id == po.vendor_id).first() if po and po.vendor_id else None)

    paid_amount = _safe_float(invoice.total_amount, None)
    if paid_amount is None:
        paid_amount = _safe_float(invoice.subtotal, 0.0) + _safe_float(invoice.taxes, 0.0)

    if project:
        for cat, amt in _allocate_amounts(paid_amount, _project_category_weights(project)):
            _upsert_ledger_row(
                db,
                project_id=project.id,
                rfq_id=rfq.id if rfq else None,
                vendor_id=vendor.id if vendor else None,
                purchase_order_id=po.id if po else None,
                shipment_id=None,
                invoice_id=invoice.id,
                ledger_type="paid",
                source_type="payment",
                source_id=str(payment.id),
                category=cat,
                region=vendor.region if vendor else None,
                currency=invoice.currency or "USD",
                quantity=None,
                unit_price=None,
                amount=amt,
                baseline_amount=_safe_float(invoice.total_amount, None),
                realized_savings=None,
                occurred_at=payment.paid_at or datetime.utcnow(),
                metadata={
                    "payment_reference": payment.payment_reference,
                    "payment_status": payment.status,
                    "actor": getattr(actor, "id", None) if actor else None,
                },
            )

    refresh_project_rollups(db, project.id if project else None)
    return paid_amount


def record_delivery_performance(
    db: Session,
    shipment: Shipment,
    project: Optional[Project] = None,
    rfq: Optional[RFQBatch] = None,
    actor: Optional[Any] = None,
):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == shipment.purchase_order_id).first()
    project = project or (db.query(Project).filter(Project.id == po.project_id).first() if po and po.project_id else None)
    rfq = rfq or (db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po and po.rfq_id else None)
    vendor = db.query(Vendor).filter(Vendor.id == po.vendor_id).first() if po and po.vendor_id else None

    period = _first_of_month(shipment.updated_at or shipment.shipped_at or datetime.utcnow())
    total = 1
    delivered = 1 if shipment.delivered_at else 0
    late = 0
    on_time_rate = 0.0
    delay_days = None
    lead_time_days = None

    if shipment.eta and shipment.delivered_at:
        delay = (shipment.delivered_at - shipment.eta).total_seconds() / 86400.0
        delay_days = max(0.0, delay)
        late = 1 if delay > 0 else 0
        delivered = 1
        on_time_rate = 1.0 if delay <= 0 else 0.0
        lead_time_days = max(0.0, (shipment.delivered_at - shipment.shipped_at).total_seconds() / 86400.0) if shipment.shipped_at else None
    elif shipment.status in ("delivered",):
        on_time_rate = 1.0

    row = (
        db.query(DeliveryPerformanceRollup)
        .filter(
            DeliveryPerformanceRollup.period_month == period,
            DeliveryPerformanceRollup.project_id == (project.id if project else None),
            DeliveryPerformanceRollup.vendor_id == (vendor.id if vendor else None),
        )
        .first()
    )
    if not row:
        row = DeliveryPerformanceRollup(
            project_id=project.id if project else None,
            vendor_id=vendor.id if vendor else None,
            vendor_name=vendor.name if vendor else None,
            period_month=period,
            currency=po.currency if po else "USD",
        )
        db.add(row)

    row.total_shipments = total
    row.on_time_shipments = delivered - late
    row.late_shipments = late
    row.on_time_rate = (row.on_time_shipments / max(row.total_shipments, 1))
    row.avg_lead_time_days = lead_time_days
    row.avg_delay_days = delay_days
    row.updated_at = datetime.utcnow()

    refresh_project_rollups(db, project.id if project else None)
    return row


def _upsert_savings_row(
    db: Session,
    project: Optional[Project],
    rfq: Optional[RFQBatch],
    vendor: Optional[Vendor],
    po: Optional[PurchaseOrder],
    invoice: Optional[Invoice],
    source_type: str,
    source_id: str,
    currency: str,
    baseline_amount: Optional[float],
    actual_amount: Optional[float],
    realized_at: Optional[datetime],
    notes: Optional[str] = None,
):
    if baseline_amount is None or actual_amount is None:
        return None

    realized = baseline_amount - actual_amount
    row = (
        db.query(SavingsRealized)
        .filter(SavingsRealized.source_type == source_type, SavingsRealized.source_id == source_id)
        .first()
    )
    if not row:
        row = SavingsRealized(
            project_id=project.id if project else None,
            rfq_id=rfq.id if rfq else None,
            vendor_id=vendor.id if vendor else None,
            purchase_order_id=po.id if po else None,
            invoice_id=invoice.id if invoice else None,
            source_type=source_type,
            source_id=source_id,
            currency=currency or "USD",
            baseline_amount=baseline_amount,
            actual_amount=actual_amount,
            realized_amount=realized,
            realized_at=realized_at or datetime.utcnow(),
            notes=notes,
            metadata_={},
        )
        db.add(row)
    else:
        row.project_id = project.id if project else row.project_id
        row.rfq_id = rfq.id if rfq else row.rfq_id
        row.vendor_id = vendor.id if vendor else row.vendor_id
        row.purchase_order_id = po.id if po else row.purchase_order_id
        row.invoice_id = invoice.id if invoice else row.invoice_id
        row.currency = currency or row.currency or "USD"
        row.baseline_amount = baseline_amount
        row.actual_amount = actual_amount
        row.realized_amount = realized
        row.realized_at = realized_at or row.realized_at or datetime.utcnow()
        row.notes = notes or row.notes
        row.updated_at = datetime.utcnow()
    return row


def refresh_project_rollups(db: Session, project_id: Optional[str] = None):
    """Recompute rollups from the spend ledger and execution tables."""
    project_filter = SpendLedger.project_id == project_id if project_id else SpendLedger.project_id.isnot(None)

    # clear existing rollups for project scope
    if project_id:
        db.query(CategorySpendRollup).filter(CategorySpendRollup.project_id == project_id).delete(synchronize_session=False)
        db.query(VendorSpendRollup).filter(VendorSpendRollup.project_id == project_id).delete(synchronize_session=False)
        db.query(MonthlySpendSnapshot).filter(MonthlySpendSnapshot.project_id == project_id).delete(synchronize_session=False)
        db.query(DeliveryPerformanceRollup).filter(DeliveryPerformanceRollup.project_id == project_id).delete(synchronize_session=False)
        db.query(SavingsRealized).filter(SavingsRealized.project_id == project_id).delete(synchronize_session=False)
    else:
        # global rebuild is more expensive but useful for backfill jobs
        db.query(CategorySpendRollup).delete(synchronize_session=False)
        db.query(VendorSpendRollup).delete(synchronize_session=False)
        db.query(MonthlySpendSnapshot).delete(synchronize_session=False)
        db.query(DeliveryPerformanceRollup).delete(synchronize_session=False)

    ledgers = db.query(SpendLedger).filter(project_filter).all()
    if not ledgers:
        return

    # category rollups
    cat_bucket = defaultdict(lambda: {"committed": 0.0, "invoiced": 0.0, "paid": 0.0, "savings": 0.0, "count": 0, "currency": "USD"})
    vendor_bucket = defaultdict(lambda: {"committed": 0.0, "invoiced": 0.0, "paid": 0.0, "savings": 0.0, "orders": 0, "currency": "USD"})
    month_bucket = defaultdict(lambda: {"committed": 0.0, "invoiced": 0.0, "paid": 0.0, "savings": 0.0, "currency": "USD"})
    region_bucket = defaultdict(float)

    for row in ledgers:
        month = _first_of_month(row.occurred_at)
        key_cat = (project_id, month, row.category or "uncategorized", row.currency or "USD")
        key_vendor = (project_id, month, str(row.vendor_id) if row.vendor_id else None, row.currency or "USD")
        key_month = (project_id, month, row.currency or "USD")

        if row.ledger_type == "committed":
            cat_bucket[key_cat]["committed"] += _safe_float(row.amount, 0.0)
            vendor_bucket[key_vendor]["committed"] += _safe_float(row.amount, 0.0)
            month_bucket[key_month]["committed"] += _safe_float(row.amount, 0.0)
        elif row.ledger_type == "invoiced":
            cat_bucket[key_cat]["invoiced"] += _safe_float(row.amount, 0.0)
            vendor_bucket[key_vendor]["invoiced"] += _safe_float(row.amount, 0.0)
            month_bucket[key_month]["invoiced"] += _safe_float(row.amount, 0.0)
        elif row.ledger_type == "paid":
            cat_bucket[key_cat]["paid"] += _safe_float(row.amount, 0.0)
            vendor_bucket[key_vendor]["paid"] += _safe_float(row.amount, 0.0)
            month_bucket[key_month]["paid"] += _safe_float(row.amount, 0.0)

        if row.realized_savings is not None:
            cat_bucket[key_cat]["savings"] += _safe_float(row.realized_savings, 0.0)
            vendor_bucket[key_vendor]["savings"] += _safe_float(row.realized_savings, 0.0)
            month_bucket[key_month]["savings"] += _safe_float(row.realized_savings, 0.0)

        if row.region:
            region_bucket[row.region] += _safe_float(row.amount, 0.0)

        cat_bucket[key_cat]["count"] += 1
        vendor_bucket[key_vendor]["orders"] += 1

    for (pid, month, category, currency), vals in cat_bucket.items():
        db.add(CategorySpendRollup(
            project_id=pid,
            period_month=month,
            category=category,
            currency=currency,
            committed_spend=vals["committed"],
            invoiced_spend=vals["invoiced"],
            paid_spend=vals["paid"],
            savings_realized=vals["savings"],
            line_count=vals["count"],
        ))

    vendor_name_cache = {}
    for (pid, month, vendor_id, currency), vals in vendor_bucket.items():
        vn = None
        if vendor_id and vendor_id not in vendor_name_cache:
            vn = _vendor_name(db, vendor_id)
            vendor_name_cache[vendor_id] = vn
        else:
            vn = vendor_name_cache.get(vendor_id)

        # shipment performance for vendor/month
        shipments = db.query(Shipment).join(PurchaseOrder, PurchaseOrder.id == Shipment.purchase_order_id)\
            .filter(PurchaseOrder.project_id == pid if pid else True).all()
        on_time_shipments = 0
        late_shipments = 0
        lead_times = []
        for s in shipments:
            if s.delivered_at:
                if s.eta and s.delivered_at <= s.eta:
                    on_time_shipments += 1
                else:
                    late_shipments += 1
                if s.shipped_at:
                    lead_times.append((_safe_float((s.delivered_at - s.shipped_at).total_seconds(), 0.0) / 86400.0))

        avg_lead = sum(lead_times) / len(lead_times) if lead_times else None
        on_time_rate = on_time_shipments / max((on_time_shipments + late_shipments), 1)

        db.add(VendorSpendRollup(
            project_id=pid,
            vendor_id=vendor_id,
            vendor_name=vn,
            period_month=month,
            currency=currency,
            committed_spend=vals["committed"],
            invoiced_spend=vals["invoiced"],
            paid_spend=vals["paid"],
            savings_realized=vals["savings"],
            total_orders=vals["orders"],
            on_time_shipments=on_time_shipments,
            late_shipments=late_shipments,
            avg_lead_time_days=avg_lead,
            on_time_rate=on_time_rate,
        ))

    for (pid, month, currency), vals in month_bucket.items():
        # quote-to-order conversion and vendor on-time rate are project-wide KPIs
        rfqs = db.query(RFQBatch).join(Project, Project.bom_id == RFQBatch.bom_id).filter(Project.id == pid if pid else True).all()
        quotes_received = 0
        orders_issued = 0
        for rfq in rfqs:
            quotes_received += db.query(SpendLedger).filter(SpendLedger.rfq_id == rfq.id, SpendLedger.ledger_type == "invoiced").count()
            orders_issued += db.query(PurchaseOrder).filter(PurchaseOrder.rfq_id == rfq.id).count()

        conversion = (orders_issued / max(len(rfqs), 1)) if rfqs else None

        vendor_rollups = db.query(VendorSpendRollup).filter(VendorSpendRollup.project_id == pid if pid else True).all()
        on_time_rates = [float(v.on_time_rate) for v in vendor_rollups if v.on_time_rate is not None]
        vendor_on_time = sum(on_time_rates) / len(on_time_rates) if on_time_rates else None

        avg_lead_times = [float(v.avg_lead_time_days) for v in vendor_rollups if v.avg_lead_time_days is not None]
        avg_lead = sum(avg_lead_times) / len(avg_lead_times) if avg_lead_times else None

        db.add(MonthlySpendSnapshot(
            project_id=pid,
            period_month=month,
            currency=currency,
            committed_spend=vals["committed"],
            invoiced_spend=vals["invoiced"],
            paid_spend=vals["paid"],
            savings_realized=vals["savings"],
            quote_to_order_conversion=conversion,
            vendor_on_time_rate=vendor_on_time,
            avg_lead_time_days=avg_lead,
        ))

    # delivery performance rollups from shipments
    shipments = db.query(Shipment).all()
    for shipment in shipments:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == shipment.purchase_order_id).first()
        project = db.query(Project).filter(Project.id == po.project_id).first() if po and po.project_id else None
        vendor = db.query(Vendor).filter(Vendor.id == po.vendor_id).first() if po and po.vendor_id else None
        period = _first_of_month(shipment.updated_at or shipment.shipped_at or datetime.utcnow())

        total = 1
        late = 0
        on_time = 0
        avg_lead = None
        avg_delay = None
        if shipment.delivered_at and shipment.eta:
            if shipment.delivered_at <= shipment.eta:
                on_time = 1
            else:
                late = 1
                avg_delay = max(0.0, (shipment.delivered_at - shipment.eta).total_seconds() / 86400.0)
            if shipment.shipped_at:
                avg_lead = max(0.0, (shipment.delivered_at - shipment.shipped_at).total_seconds() / 86400.0)

        db.add(DeliveryPerformanceRollup(
            project_id=project.id if project else None,
            vendor_id=vendor.id if vendor else None,
            vendor_name=vendor.name if vendor else None,
            period_month=period,
            currency=po.currency if po else "USD",
            total_shipments=total,
            on_time_shipments=on_time,
            late_shipments=late,
            on_time_rate=(on_time / max(total, 1)),
            avg_lead_time_days=avg_lead,
            avg_delay_days=avg_delay,
        ))

    # update project snapshot for dashboard and serialize_summary()
    if project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            totals = get_spend_analytics(db, project_id=project_id)
            project.project_metadata = project.project_metadata or {}
            project.project_metadata["analytics_snapshot"] = totals
            project.project_metadata["spend_summary"] = totals.get("totals", {})
            project.project_metadata["quote_to_order_conversion"] = totals.get("quote_to_order_conversion")
            project.project_metadata["vendor_on_time_rate"] = totals.get("vendor_on_time_rate")
            project.project_metadata["savings_realized"] = totals.get("totals", {}).get("savings_realized")
            project.project_metadata["updated_at"] = datetime.utcnow().isoformat()

    db.flush()


def get_spend_analytics(
    db: Session,
    project_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    query = db.query(SpendLedger)
    if project_id:
        query = query.filter(SpendLedger.project_id == project_id)

    if start_date:
        query = query.filter(SpendLedger.occurred_at >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.filter(SpendLedger.occurred_at <= datetime.fromisoformat(end_date))

    rows = query.all()
    totals = {
        "committed_spend": round(sum(_safe_float(r.amount, 0.0) for r in rows if r.ledger_type == "committed"), 2),
        "invoiced_spend": round(sum(_safe_float(r.amount, 0.0) for r in rows if r.ledger_type == "invoiced"), 2),
        "paid_spend": round(sum(_safe_float(r.amount, 0.0) for r in rows if r.ledger_type == "paid"), 2),
        "savings_realized": round(sum(_safe_float(r.realized_savings, 0.0) for r in rows if r.realized_savings is not None), 2),
        "ledger_rows": len(rows),
    }

    by_vendor = []
    vendor_groups = defaultdict(lambda: {"spend": 0.0, "count": 0, "currency": "USD", "vendor_id": None})
    for r in rows:
        if not r.vendor_id:
            continue
        key = str(r.vendor_id)
        vendor_groups[key]["spend"] += _safe_float(r.amount, 0.0)
        vendor_groups[key]["count"] += 1
        vendor_groups[key]["currency"] = r.currency or "USD"
        vendor_groups[key]["vendor_id"] = key

    for vid, vals in vendor_groups.items():
        vendor = db.query(Vendor).filter(Vendor.id == vid).first()
        rollup = (
            db.query(VendorSpendRollup)
            .filter(VendorSpendRollup.vendor_id == vid)
            .order_by(VendorSpendRollup.period_month.desc())
            .first()
        )
        by_vendor.append({
            "vendor_id": vid,
            "vendor_name": vendor.name if vendor else (rollup.vendor_name if rollup else None),
            "region": vendor.region if vendor else None,
            "total_spend": round(vals["spend"], 2),
            "ledger_rows": vals["count"],
            "on_time_rate": float(rollup.on_time_rate) if rollup and rollup.on_time_rate is not None else None,
            "avg_lead_time_days": float(rollup.avg_lead_time_days) if rollup and rollup.avg_lead_time_days is not None else None,
        })
    by_vendor.sort(key=lambda x: x["total_spend"], reverse=True)

    by_category = []
    cat_groups = defaultdict(float)
    for r in rows:
        cat_groups[r.category or "uncategorized"] += _safe_float(r.amount, 0.0)
    for cat, amt in cat_groups.items():
        by_category.append({"category": cat, "total_spend": round(amt, 2)})
    by_category.sort(key=lambda x: x["total_spend"], reverse=True)

    by_region = []
    region_groups = defaultdict(float)
    for r in rows:
        region_groups[r.region or "unknown"] += _safe_float(r.amount, 0.0)
    for region, amt in region_groups.items():
        by_region.append({"region": region, "total_spend": round(amt, 2)})
    by_region.sort(key=lambda x: x["total_spend"], reverse=True)

    monthly = []
    month_groups = defaultdict(lambda: {"committed": 0.0, "invoiced": 0.0, "paid": 0.0, "savings": 0.0})
    for r in rows:
        key = _month_key(r.occurred_at)
        month_groups[key]["committed" if r.ledger_type == "committed" else "invoiced" if r.ledger_type == "invoiced" else "paid" if r.ledger_type == "paid" else "paid"] += _safe_float(r.amount, 0.0)
        if r.realized_savings is not None:
            month_groups[key]["savings"] += _safe_float(r.realized_savings, 0.0)
    for month, vals in sorted(month_groups.items()):
        monthly.append({
            "month": month,
            "committed_spend": round(vals["committed"], 2),
            "invoiced_spend": round(vals["invoiced"], 2),
            "paid_spend": round(vals["paid"], 2),
            "savings_realized": round(vals["savings"], 2),
        })

    snapshots = db.query(MonthlySpendSnapshot)
    if project_id:
        snapshots = snapshots.filter(MonthlySpendSnapshot.project_id == project_id)
    latest_snapshot = snapshots.order_by(MonthlySpendSnapshot.period_month.desc()).first()

    quote_to_order_conversion = float(latest_snapshot.quote_to_order_conversion) if latest_snapshot and latest_snapshot.quote_to_order_conversion is not None else None
    vendor_on_time_rate = float(latest_snapshot.vendor_on_time_rate) if latest_snapshot and latest_snapshot.vendor_on_time_rate is not None else None

    return {
        "totals": totals,
        "by_vendor": by_vendor,
        "by_category": by_category,
        "by_region": by_region,
        "monthly": monthly,
        "quote_to_order_conversion": quote_to_order_conversion,
        "vendor_on_time_rate": vendor_on_time_rate,
        "filters": {
            "project_id": project_id,
            "start_date": start_date,
            "end_date": end_date,
        },
    }


def get_vendor_analytics(db: Session, project_id: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    if project_id:
        rows = db.query(VendorSpendRollup).filter(VendorSpendRollup.project_id == project_id).all()
    else:
        rows = db.query(VendorSpendRollup).all()
    if start_date:
        rows = [r for r in rows if r.period_month >= datetime.fromisoformat(start_date)]
    if end_date:
        rows = [r for r in rows if r.period_month <= datetime.fromisoformat(end_date)]

    vendors = []
    for r in rows:
        vendors.append({
            "vendor_id": str(r.vendor_id) if r.vendor_id else None,
            "vendor_name": r.vendor_name,
            "period_month": r.period_month.isoformat() if r.period_month else None,
            "committed_spend": _safe_float(r.committed_spend, 0.0),
            "invoiced_spend": _safe_float(r.invoiced_spend, 0.0),
            "paid_spend": _safe_float(r.paid_spend, 0.0),
            "savings_realized": _safe_float(r.savings_realized, 0.0),
            "total_orders": r.total_orders,
            "on_time_rate": _safe_float(r.on_time_rate, None),
            "avg_lead_time_days": _safe_float(r.avg_lead_time_days, None),
            "late_shipments": r.late_shipments,
            "on_time_shipments": r.on_time_shipments,
        })
    vendors.sort(key=lambda x: x["paid_spend"], reverse=True)
    return {"filters": {"project_id": project_id, "start_date": start_date, "end_date": end_date}, "vendors": vendors}


def get_category_analytics(db: Session, project_id: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    rows = db.query(CategorySpendRollup)
    if project_id:
        rows = rows.filter(CategorySpendRollup.project_id == project_id)
    rows = rows.all()
    if start_date:
        rows = [r for r in rows if r.period_month >= datetime.fromisoformat(start_date)]
    if end_date:
        rows = [r for r in rows if r.period_month <= datetime.fromisoformat(end_date)]

    categories = []
    for r in rows:
        categories.append({
            "category": r.category,
            "period_month": r.period_month.isoformat() if r.period_month else None,
            "committed_spend": _safe_float(r.committed_spend, 0.0),
            "invoiced_spend": _safe_float(r.invoiced_spend, 0.0),
            "paid_spend": _safe_float(r.paid_spend, 0.0),
            "savings_realized": _safe_float(r.savings_realized, 0.0),
            "line_count": r.line_count,
        })
    categories.sort(key=lambda x: x["paid_spend"], reverse=True)
    return {"filters": {"project_id": project_id, "start_date": start_date, "end_date": end_date}, "categories": categories}


def get_trends(db: Session, project_id: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    rows = db.query(MonthlySpendSnapshot)
    if project_id:
        rows = rows.filter(MonthlySpendSnapshot.project_id == project_id)
    rows = rows.all()
    if start_date:
        rows = [r for r in rows if r.period_month >= datetime.fromisoformat(start_date)]
    if end_date:
        rows = [r for r in rows if r.period_month <= datetime.fromisoformat(end_date)]

    monthly = []
    lead_time_trend = []
    quote_to_order = []
    vendor_on_time = []
    for r in rows:
        month = r.period_month.isoformat() if r.period_month else None
        monthly.append({
            "month": month,
            "committed_spend": _safe_float(r.committed_spend, 0.0),
            "invoiced_spend": _safe_float(r.invoiced_spend, 0.0),
            "paid_spend": _safe_float(r.paid_spend, 0.0),
            "savings_realized": _safe_float(r.savings_realized, 0.0),
        })
        lead_time_trend.append({"month": month, "avg_lead_time_days": _safe_float(r.avg_lead_time_days, None)})
        quote_to_order.append({"month": month, "quote_to_order_conversion": _safe_float(r.quote_to_order_conversion, None)})
        vendor_on_time.append({"month": month, "vendor_on_time_rate": _safe_float(r.vendor_on_time_rate, None)})

    return {
        "filters": {"project_id": project_id, "start_date": start_date, "end_date": end_date},
        "monthly": monthly,
        "lead_time_trend": lead_time_trend,
        "quote_to_order_conversion": quote_to_order,
        "vendor_on_time_rate": vendor_on_time,
    }


def get_savings(db: Session, project_id: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    rows = db.query(SavingsRealized)
    if project_id:
        rows = rows.filter(SavingsRealized.project_id == project_id)
    rows = rows.all()
    if start_date:
        rows = [r for r in rows if r.realized_at and r.realized_at >= datetime.fromisoformat(start_date)]
    if end_date:
        rows = [r for r in rows if r.realized_at and r.realized_at <= datetime.fromisoformat(end_date)]

    savings = []
    total = 0.0
    for r in rows:
        amt = _safe_float(r.realized_amount, 0.0)
        total += amt
        savings.append({
            "id": r.id,
            "project_id": r.project_id,
            "rfq_id": r.rfq_id,
            "vendor_id": r.vendor_id,
            "purchase_order_id": r.purchase_order_id,
            "invoice_id": r.invoice_id,
            "source_type": r.source_type,
            "source_id": r.source_id,
            "currency": r.currency,
            "baseline_amount": _safe_float(r.baseline_amount, None),
            "actual_amount": _safe_float(r.actual_amount, None),
            "realized_amount": amt,
            "realized_at": r.realized_at.isoformat() if r.realized_at else None,
            "notes": r.notes,
        })

    return {
        "filters": {"project_id": project_id, "start_date": start_date, "end_date": end_date},
        "savings": savings,
        "totals": {"savings_realized": round(total, 2), "count": len(rows)},
    }


def schedule_report(
    db: Session,
    *,
    report_name: str,
    report_type: str,
    frequency: str = "weekly",
    recipients_json: Optional[List[str]] = None,
    filters_json: Optional[Dict[str, Any]] = None,
    is_active: bool = True,
    next_run_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ReportSchedule:
    frequency = (frequency or "weekly").lower()
    now = datetime.utcnow()
    if not next_run_at:
        if frequency == "daily":
            next_run_at = now + timedelta(days=1)
        elif frequency == "monthly":
            next_run_at = now + timedelta(days=30)
        else:
            next_run_at = now + timedelta(days=7)

    row = ReportSchedule(
        report_name=report_name,
        report_type=report_type,
        frequency=frequency,
        recipients_json=recipients_json or [],
        filters_json=filters_json or {},
        is_active=is_active,
        next_run_at=next_run_at,
        metadata_=metadata or {},
    )
    db.add(row)
    db.flush()
    return row


def backfill_spend_ledger(db: Session, reset: bool = False):
    """Replay historical orders/invoices/payments into the ledger."""
    if reset:
        db.query(SpendLedger).delete(synchronize_session=False)
        db.query(CategorySpendRollup).delete(synchronize_session=False)
        db.query(VendorSpendRollup).delete(synchronize_session=False)
        db.query(MonthlySpendSnapshot).delete(synchronize_session=False)
        db.query(SavingsRealized).delete(synchronize_session=False)
        db.query(DeliveryPerformanceRollup).delete(synchronize_session=False)

    pos = db.query(PurchaseOrder).all()
    for po in pos:
        project = db.query(Project).filter(Project.id == po.project_id).first() if po.project_id else None
        rfq = db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po.rfq_id else None
        record_purchase_order_spend(db, po, project=project, rfq=rfq)

    invoices = db.query(Invoice).all()
    for inv in invoices:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == inv.purchase_order_id).first()
        project = db.query(Project).filter(Project.id == po.project_id).first() if po and po.project_id else None
        rfq = db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po and po.rfq_id else None
        record_invoice_spend(db, inv, po=po, project=project, rfq=rfq)

    payments = db.query(PaymentState).all()
    for p in payments:
        inv = db.query(Invoice).filter(Invoice.id == p.invoice_id).first()
        if inv:
            po = db.query(PurchaseOrder).filter(PurchaseOrder.id == p.purchase_order_id).first()
            project = db.query(Project).filter(Project.id == po.project_id).first() if po and po.project_id else None
            rfq = db.query(RFQBatch).filter(RFQBatch.id == po.rfq_id).first() if po and po.rfq_id else None
            record_payment_spend(db, p, inv, po=po, project=project, rfq=rfq)

    shipments = db.query(Shipment).all()
    for s in shipments:
        record_delivery_performance(db, s)