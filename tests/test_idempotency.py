"""Task 1: Idempotency middleware tests."""
import pytest

def test_idempotency_middleware_imports():
    from app.middleware.idempotency import IdempotencyMiddleware
    # Verify class accepts redis_getter parameter
    assert 'redis_getter' in IdempotencyMiddleware.__init__.__code__.co_varnames

def test_idempotency_no_crash_without_redis():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/v1/projects", json={},
                    headers={"Idempotency-Key": "test-key-123"})
    # Should process without crash (may return 401 for auth)
    assert r.status_code in (200, 201, 400, 401, 422)
