#!/bin/bash
# D-1: Release job — runs before web process on Railway/Heroku
# This ensures schema migrations are applied BEFORE the app starts serving.
#
# Usage:
#   Railway: set this as the release command
#   Heroku:  add to Procfile as `release: bash release.sh`
#   Manual:  bash release.sh

set -e

echo "=== PGI Hub Release Job ==="
echo "Environment: ${ENVIRONMENT:-development}"
echo ""

# Step 1: Run Alembic migrations
echo "Running database migrations..."
python -m alembic upgrade head
echo "Migrations complete."

# Step 2: Optional — seed data on first deployment
if [ "${ENABLE_RUNTIME_SEEDS}" = "true" ]; then
    echo "Running data seeds..."
    python -c "
from app.core.database import SessionLocal
from app.services import vendor_service, geo_service, seed_service

db = SessionLocal()
try:
    vendor_service.seed_vendors(db)
    geo_service.seed_geo_data(db)
    seed_service.seed_canonical_parts(db)
    db.commit()
    print('Seeds complete.')
except Exception as e:
    db.rollback()
    print(f'Seeds skipped: {e}')
finally:
    db.close()
"
fi

echo ""
echo "=== Release complete ==="
