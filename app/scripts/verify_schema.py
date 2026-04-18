"""Assert that every table in Blueprint §21 exists with required columns.
Usage: python -m app.scripts.verify_schema
Exits 0 if schema matches; 1 otherwise.
"""
from sqlalchemy import inspect, text
from app.core.database import engine

REQUIRED_TABLES = {
    "users": ["id", "email", "organization_id"],
    "organizations": ["id", "name"],
    "projects": ["id", "organization_id", "name", "target_country", "target_currency", "stage", "status"],
    "bom_uploads": ["upload_id", "project_id", "source_type", "file_name"],
    "bom_lines": ["bom_line_id", "project_id", "part_id", "raw_text", "normalized_name",
                  "category", "quantity", "status", "score_cache_json"],
    "part_master": ["part_id", "canonical_name", "category", "commodity_group",
                    "taxonomy_code", "spec_template", "default_uom", "embedding"],
    "vendor": ["id", "name", "vendor_type"],
    "vendor_capability": ["capability_id", "vendor_id"],
    "baseline_price": ["price_id", "part_id", "quantity_break", "price_floor", "price_mid",
                       "price_ceiling", "currency", "fetched_at", "freshness_status"],
    "fx_rates": ["id", "from_currency", "to_currency", "rate", "fetched_at",
                 "locked_for_quote_id", "freshness_status"],
    "tariff_schedules": ["hs_code", "from_country", "to_country", "duty_rate_pct",
                         "fta_eligible", "freshness_status"],
    "logistics_rate": ["logistics_id", "origin_country", "destination_country", "carrier",
                       "service_level", "weight_band", "cost_estimate",
                       "transit_days_min", "transit_days_max", "freshness_status"],
    "purchase_orders": ["id", "status", "incoterm", "logistics_provider", "tracking_number"],
    "shipments": ["id", "carrier", "milestone_history_json", "eta", "delay_flag"],
    "data_freshness_log": ["log_id", "table_name", "record_id", "fetched_at",
                           "source_api", "status"],
    "guest_search_log": ["search_id", "session_id", "search_query", "components_json"],
    "vendor_invite_token": ["token_id", "vendor_id", "email", "token_hash", "purpose"],
    "report_snapshot": ["snapshot_id", "organization_id", "report_type", "payload_json",
                        "ai_insight_text"],
    "approval_chain": ["chain_id", "organization_id", "name", "rules_json", "is_active"],
}

def main() -> int:
    insp = inspect(engine)
    missing_tables = []
    missing_columns = {}
    existing = set(insp.get_table_names())
    for table, cols in REQUIRED_TABLES.items():
        if table not in existing:
            missing_tables.append(table)
            continue
        actual_cols = {c["name"] for c in insp.get_columns(table)}
        miss = [c for c in cols if c not in actual_cols]
        if miss:
            missing_columns[table] = miss
    if missing_tables:
        print("MISSING TABLES:")
        for t in missing_tables: print(f"  - {t}")
    if missing_columns:
        print("\nMISSING COLUMNS:")
        for t, cols in missing_columns.items():
            print(f"  {t}: {\', \'.join(cols)}")
    if missing_tables or missing_columns:
        return 1
    print("✓ Schema matches Blueprint §21 exactly.")
    return 0

if __name__ == "__main__":
    import sys; sys.exit(main())
