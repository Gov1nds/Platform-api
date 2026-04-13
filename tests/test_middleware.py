"""Tests for middleware components."""
import pytest


class TestRequestContext:

    def test_request_id_generated_if_absent(self, test_client):
        resp = test_client.get("/health")
        assert "x-request-id" in resp.headers

    def test_request_id_preserved_if_provided(self, test_client):
        custom_id = "test-request-id-12345"
        resp = test_client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.headers.get("x-request-id") == custom_id


class TestSystemEndpoints:

    def test_health_returns_status(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "components" in data

    def test_readiness_probe(self, test_client):
        resp = test_client.get("/ready")
        assert resp.status_code in (200, 503)

    def test_liveness_probe(self, test_client):
        resp = test_client.get("/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_root_endpoint(self, test_client):
        resp = test_client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "PGI Platform"
        assert "version" in data
