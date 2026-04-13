"""Tests for authentication routes."""
import pytest


class TestAuthEndpoints:

    def test_register_creates_user(self, test_client):
        resp = test_client.post("/api/v1/auth/register", json={
            "email": "newuser@example.com",
            "password": "securepassword123",
            "full_name": "New User",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["email"] == "newuser@example.com"

    def test_register_duplicate_email(self, test_client):
        test_client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "pass123",
        })
        resp = test_client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "pass456",
        })
        assert resp.status_code == 400

    def test_login_valid(self, test_client, test_user):
        resp = test_client.post("/api/v1/auth/login", json={
            "email": test_user.email,
            "password": "testpass123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_invalid_password(self, test_client, test_user):
        resp = test_client.post("/api/v1/auth/login", json={
            "email": test_user.email,
            "password": "wrongpass",
        })
        assert resp.status_code == 401

    def test_me_requires_auth(self, test_client):
        resp = test_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_returns_user(self, test_client, auth_headers):
        resp = test_client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert "email" in resp.json()

    def test_token_exchange_password(self, test_client, test_user):
        resp = test_client.post("/api/v1/auth/token", json={
            "grant_type": "password",
            "email": test_user.email,
            "password": "testpass123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["expires_in"] == 900

    def test_dashboard_hydration(self, test_client, auth_headers):
        resp = test_client.get("/api/v1/auth/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "user" in data
        assert "kpis" in data
        assert "unread_notifications" in data

    def test_logout(self, test_client):
        resp = test_client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["status"] == "logged_out"
