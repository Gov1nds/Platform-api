"""Vendor consolidation analysis (Blueprint §26.3, C12)."""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session

def analyze_consolidation(db: Session, project_id: str) -> dict:
    rows = db.execute(text("""
        SELECT bl.bom_line_id, bl.score_cache_json
        FROM bom_lines bl
        WHERE bl.project_id = :pid AND bl.status IN ('SCORED','RFQ_PENDING','RFQ_SENT')
          AND bl.score_cache_json IS NOT NULL
    """), {"pid": project_id}).fetchall()
    vendor_lines = defaultdict(list)
    for r in rows:
        cache = r.score_cache_json or {}
        for v in (cache.get("top_vendors") or []):
            vendor_lines[v.get("vendor_id")].append({
                "bom_line_id": str(r.bom_line_id),
                "unit_price": Decimal(str(v.get("unit_price", 0))),
                "quantity": int(v.get("quantity", 1))})
    opportunities = []
    for vid, lines in vendor_lines.items():
        if len(lines) < 3: continue
        avg_freight = db.execute(text(
            "SELECT COALESCE(AVG(cost_estimate),50) FROM logistics_rate "
            "WHERE destination_country = (SELECT target_country FROM projects WHERE id = :pid)"
        ), {"pid": project_id}).scalar() or 50
        logistics_savings = Decimal(str(avg_freight)) * (len(lines) - 1)
        total_value = sum(l["unit_price"] * l["quantity"] for l in lines)
        volume_discount = total_value * Decimal("0.06")
        opportunities.append({
            "vendor_id": vid, "lines_covered": len(lines),
            "line_ids": [l["bom_line_id"] for l in lines],
            "estimated_logistics_savings": float(logistics_savings),
            "estimated_volume_discount": float(volume_discount),
            "total_estimated_savings": float(logistics_savings + volume_discount),
            "total_value": float(total_value),
            "rationale": f"Vendor covers {len(lines)} BOM lines. ~{logistics_savings:.0f} freight + ~{volume_discount:.0f} volume savings."})
    opportunities.sort(key=lambda x: -x["total_estimated_savings"])
    return {"project_id": project_id, "opportunities": opportunities[:5]}
