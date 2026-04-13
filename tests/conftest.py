"""
Pytest fixtures for PGI Platform API tests.

Provides test database sessions, FastAPI test client,
and factory fixtures for creating test entities.

References: GAP-034 (test infrastructure)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Override settings before importing app
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("LEGACY_HS256_ENABLED", "true")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("OTEL_ENABLED", "false")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///./test.db")

engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False} if "sqlite" in TEST_DB_URL else {})
TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Create tables and yield a test database session, then rollback."""
    Base.metadata.create_all(bind=engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_client(db_session: Session) -> TestClient:
    """FastAPI TestClient with DB session override."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def test_org(db_session: Session):
    """Create a test organization."""
    from app.models.user import Organization

    org = Organization(
        name="Test Org",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        type="buyer",
    )
    db_session.add(org)
    db_session.flush()
    return org


@pytest.fixture
def test_user(db_session: Session, test_org):
    """Create a test user with organization."""
    from app.models.user import User, OrganizationMembership

    user = User(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("testpass123"),
        full_name="Test User",
        role="BUYER_EDITOR",
        organization_id=test_org.id,
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(OrganizationMembership(
        organization_id=test_org.id,
        user_id=user.id,
        role="BUYER_EDITOR",
        accepted_at=datetime.now(timezone.utc),
    ))
    db_session.flush()
    return user


@pytest.fixture
def auth_headers(test_user) -> dict:
    """Authorization headers with valid access token."""
    token = create_access_token({
        "sub": test_user.id,
        "email": test_user.email,
        "role": test_user.role,
        "organization_id": test_user.organization_id,
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_vendor(db_session: Session):
    """Create a test vendor."""
    from app.models.vendor import Vendor

    vendor = Vendor(
        name="Test Vendor Inc.",
        status="BASIC",
        country="US",
        is_active=True,
    )
    db_session.add(vendor)
    db_session.flush()
    return vendor


@pytest.fixture
def test_vendor_user(db_session: Session, test_vendor):
    """Create a test vendor user."""
    from app.models.user import VendorUser

    vu = VendorUser(
        vendor_id=test_vendor.id,
        email=f"vendor-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("vendorpass123"),
        full_name="Vendor Rep",
        role="VENDOR_REP",
    )
    db_session.add(vu)
    db_session.flush()
    return vu


@pytest.fixture
def test_guest_session(db_session: Session):
    """Create a test guest session."""
    from app.models.user import GuestSession

    gs = GuestSession(
        session_token=str(uuid.uuid4()),
        status="ACTIVE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        detected_currency="USD",
    )
    db_session.add(gs)
    db_session.flush()
    return gs


@pytest.fixture
def test_project(db_session: Session, test_user, test_org):
    """Create a test project with BOM."""
    from app.models.bom import BOM
    from app.models.project import Project

    bom = BOM(
        uploaded_by_user_id=test_user.id,
        organization_id=test_org.id,
        source_file_name="test.csv",
        status="INGESTED",
    )
    db_session.add(bom)
    db_session.flush()

    project = Project(
        bom_id=bom.id,
        user_id=test_user.id,
        organization_id=test_org.id,
        name="Test Project",
        status="DRAFT",
    )
    db_session.add(project)
    db_session.flush()
    return project
