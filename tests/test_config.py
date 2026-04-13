"""Tests for app/core/config.py."""
import os
import pytest


def test_config_loads_all_required_env_vars():
    from app.core.config import Settings
    s = Settings()
    assert s.PROJECT_NAME == "PGI Platform"
    assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert s.REFRESH_TOKEN_EXPIRE_DAYS == 7
    assert s.GUEST_SESSION_COOKIE_NAME == "pgi_guest"


def test_config_production_validation_raises_on_default_secret():
    from app.core.config import Settings
    s = Settings()
    s.ENVIRONMENT = "production"
    s.SECRET_KEY = "dev-secret-change-me"
    with pytest.raises(RuntimeError, match="SECRET_KEY must not start with 'dev-'"):
        s.validate_production()


def test_config_is_production_property():
    from app.core.config import Settings
    s = Settings()
    s.ENVIRONMENT = "production"
    assert s.is_production is True
    assert s.is_staging is False
    assert s.is_development is False


def test_config_is_development_property():
    from app.core.config import Settings
    s = Settings()
    s.ENVIRONMENT = "development"
    assert s.is_development is True
    assert s.is_production is False
