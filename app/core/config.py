"""Application configuration from environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "PGI Manufacturing Intelligence Platform"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./pgi_platform.db"
    )

    # JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "pgi-dev-secret-change-in-production-2024")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # External APIs
    OCTOPART_API_KEY: str = os.getenv("OCTOPART_API_KEY", "")
    MOUSER_API_KEY: str = os.getenv("MOUSER_API_KEY", "")
    DIGIKEY_CLIENT_ID: str = os.getenv("DIGIKEY_CLIENT_ID", "")
    DIGIKEY_CLIENT_SECRET: str = os.getenv("DIGIKEY_CLIENT_SECRET", "")
    MISUMI_API_KEY: str = os.getenv("MISUMI_API_KEY", "")

    # File upload
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    MAX_FILE_SIZE_MB: int = 20

    # Analyzer path
    ANALYZER_PATH: str = os.getenv("ANALYZER_PATH", "")

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.DATABASE_URL

settings = Settings()
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
# Add to Settings class:
BOM_ANALYZER_URL: str = os.getenv(
    "BOM_ANALYZER_URL",
    "http://localhost:8000"  # default for local dev
)
# Add these to the Settings class in app/core/config.py:

# BOM Analyzer microservice
BOM_ANALYZER_URL: str = os.getenv("BOM_ANALYZER_URL", "http://localhost:8000")
INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "")

# Project statuses
PROJECT_STATUSES = [
    "uploaded", "analyzed", "quoting",
    "quoted", "approved", "in_production",
    "qc_inspection", "shipped", "completed"
]
