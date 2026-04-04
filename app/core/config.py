"""Application configuration from environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _normalize_database_url(value: str | None) -> str:
    default = "postgresql+psycopg2://postgres:postgres@localhost:5432/pgi_platform"
    url = value or default
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    PROJECT_NAME: str = "PGI Manufacturing Intelligence Platform"
    VERSION: str = "2.1.0"
    API_PREFIX: str = "/api/v1"

    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DATABASE_URL: str = _normalize_database_url(os.getenv("DATABASE_URL"))

    _secret = os.getenv("SECRET_KEY")
    SECRET_KEY: str = _secret if _secret else ("pgi-dev-secret-change-in-production-2024" if ENVIRONMENT != "production" else "")
    if ENVIRONMENT == "production" and not SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set in production")

    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    OCTOPART_API_KEY: str = os.getenv("OCTOPART_API_KEY", "")
    MOUSER_API_KEY: str = os.getenv("MOUSER_API_KEY", "")
    DIGIKEY_CLIENT_ID: str = os.getenv("DIGIKEY_CLIENT_ID", "")
    DIGIKEY_CLIENT_SECRET: str = os.getenv("DIGIKEY_CLIENT_SECRET", "")
    MISUMI_API_KEY: str = os.getenv("MISUMI_API_KEY", "")

    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    MAX_FILE_SIZE_MB: int = 20

    ANALYZER_PATH: str = os.getenv("ANALYZER_PATH", "")

    BOM_ANALYZER_URL: str = os.getenv(
        "BOM_ANALYZER_URL",
        "http://bom-intelligence-engine.railway.internal:8000",
    )
    INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "")

    OBJECT_STORAGE_PROVIDER: str = os.getenv(
        "OBJECT_STORAGE_PROVIDER",
        os.getenv("DRAWING_STORAGE_PROVIDER", "s3" if ENVIRONMENT == "production" else "local"),
    ).strip().lower()
    OBJECT_STORAGE_BUCKET: str = os.getenv("OBJECT_STORAGE_BUCKET", os.getenv("DRAWING_S3_BUCKET", ""))
    OBJECT_STORAGE_REGION: str = os.getenv("OBJECT_STORAGE_REGION", os.getenv("AWS_REGION", "us-east-1"))
    OBJECT_STORAGE_PREFIX: str = os.getenv("OBJECT_STORAGE_PREFIX", "documents/")
    OBJECT_STORAGE_PUBLIC_BASE_URL: str = os.getenv("OBJECT_STORAGE_PUBLIC_BASE_URL", "")

    REQUIRE_OBJECT_STORAGE_IN_PRODUCTION: bool = _env_bool(
        "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION",
        "true" if ENVIRONMENT == "production" else "false",
    )
    ANALYZER_READINESS_REQUIRED: bool = _env_bool(
        "ANALYZER_READINESS_REQUIRED",
        "true" if ENVIRONMENT == "production" else "false",
    )

    INTEGRATION_WEBHOOK_SECRET: str = os.getenv("INTEGRATION_WEBHOOK_SECRET", "")
    VENDOR_WEBHOOK_SECRET: str = os.getenv("VENDOR_WEBHOOK_SECRET", INTEGRATION_WEBHOOK_SECRET)
    CARRIER_WEBHOOK_SECRET: str = os.getenv("CARRIER_WEBHOOK_SECRET", INTEGRATION_WEBHOOK_SECRET)
    ERP_SYNC_SECRET: str = os.getenv("ERP_SYNC_SECRET", INTEGRATION_WEBHOOK_SECRET)
    EMAIL_INGEST_SECRET: str = os.getenv("EMAIL_INGEST_SECRET", INTEGRATION_WEBHOOK_SECRET)

    ENABLE_INTEGRATION_OBSERVABILITY: bool = _env_bool("ENABLE_INTEGRATION_OBSERVABILITY", "true")

    ENABLE_RUNTIME_BOOTSTRAP: bool = _env_bool(
        "ENABLE_RUNTIME_BOOTSTRAP",
        "true" if ENVIRONMENT != "production" else "false",
    )
    ENABLE_RUNTIME_SCHEMA_BOOTSTRAP: bool = _env_bool(
        "ENABLE_RUNTIME_SCHEMA_BOOTSTRAP",
        "true" if ENVIRONMENT != "production" else "false",
    )
    ENABLE_RUNTIME_MIGRATIONS: bool = _env_bool(
        "ENABLE_RUNTIME_MIGRATIONS",
        "true" if ENVIRONMENT != "production" else "false",
    )
    ENABLE_RUNTIME_SEEDS: bool = _env_bool(
        "ENABLE_RUNTIME_SEEDS",
        "true" if ENVIRONMENT != "production" else "false",
    )
    ENABLE_RUNTIME_PRICE_EXPIRY: bool = _env_bool(
        "ENABLE_RUNTIME_PRICE_EXPIRY",
        "true" if ENVIRONMENT != "production" else "false",
    )
    ENABLE_RUNTIME_MEMORY_DECAY: bool = _env_bool(
        "ENABLE_RUNTIME_MEMORY_DECAY",
        "true" if ENVIRONMENT != "production" else "false",
    )

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in (self.DATABASE_URL or "")

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in (self.DATABASE_URL or "")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


settings = Settings()
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

PROJECT_STATUSES = [
    "draft",
    "guest_preview",
    "project_hydrated",
    "strategy",
    "vendor_match",
    "rfq_pending",
    "rfq_sent",
    "quote_compare",
    "negotiation",
    "vendor_selected",
    "po_issued",
    "in_production",
    "qc_inspection",
    "shipped",
    "delivered",
    "spend_recorded",
    "completed",
    "cancelled",
    "archived",
    "error",
]