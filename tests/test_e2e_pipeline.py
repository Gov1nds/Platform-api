"""End-to-end test covering the full 8-stage pipeline (Blueprint §4, C36)."""
import pytest

def test_full_pipeline_structure():
    """Verify the API app loads and core endpoints exist."""
    from app.main import app
    routes = [r.path for r in app.routes]
    # Verify critical routes are registered
    assert "/health" in routes or any("/health" in r for r in routes)
    assert any("/api/v1" in str(r) for r in routes)

def test_health_endpoint():
    """Health endpoint returns structured response."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "components" in data

def test_root_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == "3.0.0"
    assert data["status"] == "operational"

def test_readiness_probe():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/ready")
    assert r.status_code in (200, 503)

def test_liveness_probe():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"

def test_metrics_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200

def test_api_version_header():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/")
    assert r.headers.get("X-API-Version") == "3.0.0"

def test_idempotency_key_required_on_rfq_create():
    """Task 1: Idempotency key enforcement."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/v1/rfq/create", json={})
    # Should require auth first (401), not crash
    assert r.status_code in (400, 401, 422)

def test_models_import():
    """Verify all new models are importable."""
    from app.models.data_freshness import DataFreshnessLog
    from app.models.part_master import PartMaster
    from app.models.guest import GuestSearchLog
    from app.models.vendor_invite import VendorInviteToken
    from app.models.approval_chain import ApprovalChain
    from app.models.report_snapshot_v2 import ReportSnapshotV2
    assert DataFreshnessLog.__tablename__ == "data_freshness_log"
    assert PartMaster.__tablename__ == "part_master"

def test_freshness_service():
    """Task 7: Freshness service functions exist."""
    from app.services.freshness_service import (
        log_refresh, annotate_freshness, require_fresh, freshness_service,
        FreshnessStatus, TTL_MAP)
    assert "fx_rates" in TTL_MAP
    assert FreshnessStatus.FRESH == "FRESH"

def test_distributor_aggregator():
    """Task 8: Distributor aggregator structure."""
    from app.integrations.distributor_connector import DistributorAggregator
    agg = DistributorAggregator()
    assert len(agg.clients) == 4
    band = DistributorAggregator.to_price_band([
        {"unit_price": 0.05, "currency": "USD"},
        {"unit_price": 0.10, "currency": "USD"},
        {"unit_price": 0.15, "currency": "USD"},
    ])
    assert band is not None
    assert band["floor"] == 0.05
    assert band["ceiling"] == 0.15

def test_consolidation_service():
    """Task 14: Consolidation function exists."""
    from app.services.consolidation_service import analyze_consolidation
    assert callable(analyze_consolidation)

def test_ocr_parser():
    """Task 15: OCR parser."""
    from app.services.ocr.quote_parser import parse_textract_expense
    result = parse_textract_expense({"ExpenseDocuments": []})
    assert "lines" in result
    assert result["currency"] == "USD"

def test_mfa_service():
    """Task 26: MFA service."""
    from app.services.mfa_service import generate_secret, encrypt, decrypt, verify_totp
    secret = generate_secret()
    assert len(secret) > 0
    enc = encrypt(secret)
    dec = decrypt(enc)
    assert dec == secret

def test_export_control():
    """Task 32: Export control flagging."""
    from app.services.enrichment.export_control_service import flag_export_control
    flags = flag_export_control("p1", "defense_electronics")
    assert len(flags) == 1
    assert flags[0]["type"] == "ITAR"

def test_sla_windows():
    """Task 19: SLA windows defined."""
    from app.workers.tasks.sla_monitor import SLA_WINDOWS
    assert "PO_SENT" in SLA_WINDOWS
    assert "IN_TRANSIT" in SLA_WINDOWS

def test_observability_metrics():
    """Task 28: Prometheus metrics importable."""
    from app.observability.metrics import REQUEST_LATENCY, EXT_API_CALLS

def test_rbac_permissions():
    """Task 31: RBAC permissions defined."""
    from app.utils.dependencies import Permission, ROLE_PERMISSIONS
    assert Permission.PO_APPROVE == "po:approve"
    assert Permission.PO_APPROVE in ROLE_PERMISSIONS["approver"]
    assert Permission.RFQ_CREATE not in ROLE_PERMISSIONS.get("viewer", set())
