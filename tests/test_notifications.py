"""Tests for notification routes."""
import pytest


class TestNotificationRoutes:

    def test_list_notifications_requires_auth(self, test_client):
        resp = test_client.get("/api/v1/notifications")
        assert resp.status_code == 401

    def test_list_notifications_empty(self, test_client, auth_headers):
        resp = test_client.get("/api/v1/notifications", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total_count"] == 0

    def test_mark_all_read(self, test_client, auth_headers):
        resp = test_client.post("/api/v1/notifications/mark-all-read", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["marked"] == 0
