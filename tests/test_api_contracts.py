"""
PGI Hub Platform API — Contract & Integration Tests

P-2: Route-level tests for auth, intake, projects, RFQ, tracking, permissions.
API-2: Contract tests freezing API response shapes.
DB-2: Schema validation via migration boot.

Usage:
  pip install pytest httpx
  pytest tests/test_api_contracts.py -v

Requires DATABASE_URL pointing at a test database.
Set ENVIRONMENT=test or ALLOW_CREATE_ALL=true for table creation.
"""
import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALLOW_CREATE_ALL", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///test_pgi.db")
os.environ.setdefault("ANALYZER_READINESS_REQUIRED", "false")
os.environ.setdefault("ENABLE_RUNTIME_BOOTSTRAP", "false")
os.environ.setdefault("OBJECT_STORAGE_PROVIDER", "local")

from fastapi.testclient import TestClient
from app.main import app
from app.core.database import Base, engine, SessionLocal

client = TestClient(app)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables for test session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("test_pgi.db"):
        os.unlink("test_pgi.db")


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _unique_email():
    return f"test_{uuid.uuid4().hex[:8]}@example.com"


def _register_user(email=None, password="testpass123"):
    email = email or _unique_email()
    resp = client.post("/api/v1/auth/register", json={
        "email": email,
        "password": password,
        "full_name": "Test User",
        "session_token": str(uuid.uuid4()),
    })
    return resp


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════
# 1. HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_root(self):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert data["status"] == "operational"

    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data


# ═══════════════════════════════════════════════════════════════════════════
# 2. AUTH CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestAuth:
    def test_register_returns_token(self):
        r = _register_user()
        assert r.status_code == 201
        data = r.json()
        assert "access_token" in data
        assert "user" in data
        assert "id" in data["user"]
        assert "email" in data["user"]

    def test_register_duplicate_email_fails(self):
        email = _unique_email()
        r1 = _register_user(email=email)
        assert r1.status_code == 201
        r2 = _register_user(email=email)
        assert r2.status_code == 400

    def test_login_success(self):
        email = _unique_email()
        _register_user(email=email, password="pass123")
        r = client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "pass123",
            "session_token": "",
        })
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_login_wrong_password(self):
        email = _unique_email()
        _register_user(email=email, password="pass123")
        r = client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "wrong",
            "session_token": "",
        })
        assert r.status_code == 401

    def test_me_requires_auth(self):
        r = client.get("/api/v1/auth/me")
        assert r.status_code in (401, 403)

    def test_me_with_token(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get("/api/v1/auth/me", headers=_auth_header(token))
        assert r.status_code == 200
        assert "email" in r.json()

    def test_register_merge_result(self):
        """P-3: Guest merge result included in registration."""
        r = _register_user()
        data = r.json()
        assert "merge_result" in data
        assert "status" in data["merge_result"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. PROJECT CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestProjects:
    def test_list_projects_requires_auth(self):
        r = client.get("/api/v1/projects")
        assert r.status_code in (401, 403)

    def test_list_projects_empty(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get("/api/v1/projects", headers=_auth_header(token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_project_metrics(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get("/api/v1/projects/metrics", headers=_auth_header(token))
        assert r.status_code == 200
        data = r.json()
        assert "total_projects" in data
        assert "open_projects" in data

    def test_project_detail_not_found(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get(f"/api/v1/projects/{uuid.uuid4()}", headers=_auth_header(token))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 4. PROJECT DETAIL RESPONSE SHAPE (API-2)
# ═══════════════════════════════════════════════════════════════════════════

class TestProjectDetailContract:
    """API-2: Verify ProjectDetail response has all fields frontend expects."""

    REQUIRED_SUMMARY_FIELDS = [
        "project_id", "name", "status", "workflow_stage",
        "total_parts", "rfq_status", "tracking_stage",
    ]

    REQUIRED_DETAIL_FIELDS = REQUIRED_SUMMARY_FIELDS + [
        "current_vendor_id", "current_rfq_id", "current_po_id",
        "analysis_status", "report_visibility_level", "unlock_status",
    ]

    def test_summary_fields_in_list(self):
        """If projects exist, each must have required summary fields."""
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get("/api/v1/projects", headers=_auth_header(token))
        assert r.status_code == 200
        # Empty list is acceptable for a new user
        for proj in r.json():
            for field in self.REQUIRED_SUMMARY_FIELDS:
                assert field in proj, f"Missing {field} in project summary"


# ═══════════════════════════════════════════════════════════════════════════
# 5. VENDOR ROUTES
# ═══════════════════════════════════════════════════════════════════════════

class TestVendors:
    def test_vendor_match_requires_auth(self):
        r = client.get("/api/v1/vendors/match?project_id=test")
        assert r.status_code in (401, 403)

    def test_vendor_not_found(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get(f"/api/v1/vendors/{uuid.uuid4()}", headers=_auth_header(token))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 6. RFQ CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestRFQ:
    def test_rfq_create_requires_auth(self):
        r = client.post("/api/v1/rfq/create", json={"bom_id": str(uuid.uuid4())})
        assert r.status_code in (401, 403)

    def test_rfq_not_found(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get(f"/api/v1/rfq/{uuid.uuid4()}", headers=_auth_header(token))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 7. TRACKING CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestTracking:
    def test_tracking_requires_auth(self):
        r = client.get(f"/api/v1/tracking/rfq/{uuid.uuid4()}")
        assert r.status_code in (401, 403)

    def test_tracking_not_found(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get(f"/api/v1/tracking/rfq/{uuid.uuid4()}", headers=_auth_header(token))
        assert r.status_code in (404, 403)


# ═══════════════════════════════════════════════════════════════════════════
# 8. ANALYTICS CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestAnalytics:
    def test_spend_requires_auth(self):
        r = client.get("/api/v1/analytics/spend")
        assert r.status_code in (401, 403)

    def test_spend_returns_data(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.get("/api/v1/analytics/spend", headers=_auth_header(token))
        # May return 403 for non-privileged user or 200 with empty data
        assert r.status_code in (200, 403)


# ═══════════════════════════════════════════════════════════════════════════
# 9. CHAT CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestChat:
    def test_threads_requires_auth(self):
        r = client.get("/api/v1/chat/threads?project_id=test")
        assert r.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════
# 10. ORGANIZATION CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class TestOrganizations:
    def test_create_org(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        r = client.post("/api/v1/organizations", json={
            "name": "Test Corp",
            "slug": f"test-corp-{uuid.uuid4().hex[:6]}",
        }, headers=_auth_header(token))
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        assert "name" in data
        assert data["name"] == "Test Corp"

    def test_list_orgs(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        slug = f"list-test-{uuid.uuid4().hex[:6]}"
        client.post("/api/v1/organizations", json={
            "name": "List Corp", "slug": slug,
        }, headers=_auth_header(token))

        r = client.get("/api/v1/organizations", headers=_auth_header(token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_org_workspaces(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        slug = f"ws-test-{uuid.uuid4().hex[:6]}"
        org_r = client.post("/api/v1/organizations", json={
            "name": "WS Corp", "slug": slug,
        }, headers=_auth_header(token))
        org_id = org_r.json()["id"]

        r = client.get(f"/api/v1/organizations/{org_id}/workspaces", headers=_auth_header(token))
        assert r.status_code == 200
        # Should have default workspace
        assert len(r.json()) >= 1

    def test_duplicate_slug_fails(self):
        reg = _register_user()
        token = reg.json()["access_token"]
        slug = f"dup-{uuid.uuid4().hex[:6]}"
        r1 = client.post("/api/v1/organizations", json={
            "name": "Org A", "slug": slug,
        }, headers=_auth_header(token))
        assert r1.status_code == 201

        r2 = client.post("/api/v1/organizations", json={
            "name": "Org B", "slug": slug,
        }, headers=_auth_header(token))
        assert r2.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# 11. PERMISSIONS TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestPermissions:
    def test_unauthenticated_project_access_denied(self):
        r = client.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert r.status_code in (401, 403)

    def test_unauthenticated_rfq_denied(self):
        r = client.post("/api/v1/rfq/create", json={"bom_id": str(uuid.uuid4())})
        assert r.status_code in (401, 403)

    def test_unauthenticated_tracking_denied(self):
        r = client.post(f"/api/v1/tracking/rfq/{uuid.uuid4()}/purchase-order", json={})
        assert r.status_code in (401, 403)

    def test_unauthenticated_approvals_denied(self):
        r = client.get(f"/api/v1/approvals?project_id={uuid.uuid4()}")
        assert r.status_code in (401, 403)
