"""
Platform configuration.

Loads all settings from environment variables. In production, required
secrets are validated at startup — the app will refuse to boot with
default/missing credentials.

References: GAP-008, GAP-032, GAP-001, GAP-007, GAP-031, GAP-015,
            GAP-003, architecture.md CC-02, CC-11, CC-12
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _int(name: str, default: str) -> int:
    return int(os.getenv(name, default))


def _db_url() -> str:
    raw = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/pgi",
    )
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)
    elif raw.startswith("postgresql://") and "+psycopg2" not in raw:
        raw = raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


class Settings:
    # ── Core ─────────────────────────────────────────────────────────────
    PROJECT_NAME: str = "PGI Platform"
    VERSION: str = "3.0.0"
    API_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DATABASE_URL: str = _db_url()
    READ_REPLICA_URL: str = os.getenv("READ_REPLICA_URL", "")

    # ── Secrets / JWT (GAP-008, GAP-032) ─────────────────────────────────
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "RS256")
    JWT_PRIVATE_KEY_PATH: str = os.getenv("JWT_PRIVATE_KEY_PATH", "")
    JWT_PUBLIC_KEY_PATH: str = os.getenv("JWT_PUBLIC_KEY_PATH", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = _int("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    REFRESH_TOKEN_EXPIRE_DAYS: int = _int("REFRESH_TOKEN_EXPIRE_DAYS", "7")

    # ── Legacy HS256 fallback (transition period) ────────────────────────
    LEGACY_HS256_ENABLED: bool = _bool("LEGACY_HS256_ENABLED", "true")

    # ── Guest Sessions (GAP-001) ─────────────────────────────────────────
    GUEST_SESSION_COOKIE_NAME: str = os.getenv("GUEST_SESSION_COOKIE_NAME", "pgi_guest")
    GUEST_SESSION_MAX_AGE_DAYS: int = _int("GUEST_SESSION_MAX_AGE_DAYS", "30")

    # ── OAuth Providers (GAP-008, INT-008) ───────────────────────────────
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    LINKEDIN_CLIENT_ID: str = os.getenv("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET: str = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    MICROSOFT_CLIENT_ID: str = os.getenv("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET: str = os.getenv("MICROSOFT_CLIENT_SECRET", "")
    MICROSOFT_TENANT_ID: str = os.getenv("MICROSOFT_TENANT_ID", "")
    SAML_METADATA_URL: str = os.getenv("SAML_METADATA_URL", "")

    # ── Object Storage (INT-007) ─────────────────────────────────────────
    S3_BUCKET: str = os.getenv("S3_BUCKET", "")
    S3_REGION: str = os.getenv("S3_REGION", "us-east-1")
    S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "")
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")

    # ── Redis (idempotency, caching, Celery) ─────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ── Notification Providers (INT-006, GAP-007) ────────────────────────
    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
    FIREBASE_CREDENTIALS_PATH: str = os.getenv("FIREBASE_CREDENTIALS_PATH", "")
    FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "")
    SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")

    # ── Distributor APIs (INT-001) ───────────────────────────────────────
    DIGIKEY_CLIENT_ID: str = os.getenv("DIGIKEY_CLIENT_ID", "")
    DIGIKEY_CLIENT_SECRET: str = os.getenv("DIGIKEY_CLIENT_SECRET", "")
    MOUSER_API_KEY: str = os.getenv("MOUSER_API_KEY", "")
    OCTOPART_API_KEY: str = os.getenv("OCTOPART_API_KEY", "")
    ARROW_API_KEY: str = os.getenv("ARROW_API_KEY", "")

    # ── Market Data APIs (INT-002, INT-003) ──────────────────────────────
    LME_API_KEY: str = os.getenv("LME_API_KEY", "")
    FASTMARKETS_API_KEY: str = os.getenv("FASTMARKETS_API_KEY", "")
    OPEN_EXCHANGE_RATES_APP_ID: str = os.getenv("OPEN_EXCHANGE_RATES_APP_ID", "")
    XE_API_KEY: str = os.getenv("XE_API_KEY", "")

    # ── Carrier / Logistics APIs (INT-005) ───────────────────────────────
    AFTERSHIP_API_KEY: str = os.getenv("AFTERSHIP_API_KEY", "")
    AFTERSHIP_WEBHOOK_SECRET: str = os.getenv("AFTERSHIP_WEBHOOK_SECRET", "")
    DHL_API_KEY: str = os.getenv("DHL_API_KEY", "")
    DHL_API_SECRET: str = os.getenv("DHL_API_SECRET", "")
    FEDEX_API_KEY: str = os.getenv("FEDEX_API_KEY", "")
    UPS_CLIENT_ID: str = os.getenv("UPS_CLIENT_ID", "")
    UPS_CLIENT_SECRET: str = os.getenv("UPS_CLIENT_SECRET", "")

    # ── Virus Scanning (INT-007) ─────────────────────────────────────────
    CLAMAV_HOST: str = os.getenv("CLAMAV_HOST", "localhost")
    CLAMAV_PORT: int = _int("CLAMAV_PORT", "3310")

    # ── Geolocation ──────────────────────────────────────────────────────
    MAXMIND_ACCOUNT_ID: str = os.getenv("MAXMIND_ACCOUNT_ID", "")
    MAXMIND_LICENSE_KEY: str = os.getenv("MAXMIND_LICENSE_KEY", "")
    MAXMIND_DB_PATH: str = os.getenv("MAXMIND_DB_PATH", "")

    # ── BOM Intelligence Engine ──────────────────────────────────────────
    BOM_ANALYZER_URL: str = os.getenv("BOM_ANALYZER_URL", "http://localhost:8001")
    INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "")

    # ── Observability (NFR-002, GAP-015) ─────────────────────────────────
    OTEL_ENABLED: bool = _bool("OTEL_ENABLED")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    OTEL_SERVICE_NAME: str = os.getenv("OTEL_SERVICE_NAME", "platform-api")

    # ── Event Broker ─────────────────────────────────────────────────────
    EVENT_BROKER_URL: str = os.getenv("EVENT_BROKER_URL", "")
    EVENT_BROKER_TYPE: str = os.getenv("EVENT_BROKER_TYPE", "memory")

    # ── Legacy (to be deprecated) ────────────────────────────────────────
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:5173,http://localhost:3000",
        ).split(",")
        if o.strip()
    ]

    # ── Convenience properties ───────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_staging(self) -> bool:
        return self.ENVIRONMENT == "staging"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"

    # ── Production guard (GAP-032) ───────────────────────────────────────

    _PRODUCTION_REQUIRED = [
        "SECRET_KEY",
        "DATABASE_URL",
        "S3_BUCKET",
        "REDIS_URL",
        "INTERNAL_API_KEY",
    ]

    def validate_production(self) -> None:
        """Raise RuntimeError if production config is invalid."""
        if not self.is_production:
            return

        if self.SECRET_KEY.startswith("dev-"):
            raise RuntimeError(
                "SECRET_KEY must not start with 'dev-' in production"
            )

        for field in self._PRODUCTION_REQUIRED:
            value = getattr(self, field, None)
            if not value:
                raise RuntimeError(
                    f"Missing required production config: {field}"
                )

        if self.JWT_ALGORITHM == "RS256":
            if not self.JWT_PRIVATE_KEY_PATH or not self.JWT_PUBLIC_KEY_PATH:
                raise RuntimeError(
                    "JWT_PRIVATE_KEY_PATH and JWT_PUBLIC_KEY_PATH are required "
                    "when JWT_ALGORITHM=RS256 in production"
                )


settings = Settings()
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
settings.validate_production()

 # ── Notification Providers (INT-006, GAP-007) ────────────────────────
SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
FIREBASE_CREDENTIALS_PATH: str = os.getenv("FIREBASE_CREDENTIALS_PATH", "")
FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "")

# ── Tracking / Logistics APIs ────────────────────────────────────────
AFTERSHIP_API_KEY: str = os.getenv("AFTERSHIP_API_KEY", "")
AFTERSHIP_WEBHOOK_SECRET: str = os.getenv("AFTERSHIP_WEBHOOK_SECRET", "")

# ── Geolocation ──────────────────────────────────────────────────────
MAXMIND_ACCOUNT_ID: str = os.getenv("MAXMIND_ACCOUNT_ID", "")
MAXMIND_LICENSE_KEY: str = os.getenv("MAXMIND_LICENSE_KEY", "")
MAXMIND_DB_PATH: str = os.getenv("MAXMIND_DB_PATH", "")