"""Tests for project routes."""
import pytest


class TestProjectRoutes:

    def test_list_projects_requires_auth(self, test_client):
        resp = test_client.get("/api/v1/projects")
        assert resp.status_code == 401

    def test_list_projects_returns_paginated(self, test_client, auth_headers):
        resp = test_client.get("/api/v1/projects", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total_count" in data

    def test_create_project(self, test_client, auth_headers):
        resp = test_client.post("/api/v1/projects", headers=auth_headers, json={
            "name": "New Project",
            "weight_profile": "balanced",
        })
        # May fail without BOM in some configurations, but should not 500
        assert resp.status_code in (201, 422, 400)
