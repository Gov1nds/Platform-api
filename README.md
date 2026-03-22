# PGI Manufacturing Intelligence Platform — Backend API

## Overview

Manufacturing decision engine + procurement strategy platform + RFQ execution system.

**Architecture:** Frontend → Platform API (FastAPI + PostgreSQL) → BOM Analyzer Engine

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your settings

# 3. Run
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Open docs
# http://localhost:8000/docs
```

## API Endpoints

### Auth
- `POST /api/v1/auth/register` — Create account
- `POST /api/v1/auth/login` — Login, get JWT

### BOM (Core Flow)
- `POST /api/v1/bom/preview` — Upload BOM → get preview (no login required)
- `POST /api/v1/bom/unlock` — Unlock full report (login or session token)

### Analysis
- `GET /api/v1/analysis/{id}` — Get stored analysis
- `GET /api/v1/analysis/bom/{bom_id}` — Get analysis by BOM

### RFQ
- `POST /api/v1/rfq/create` — Create RFQ from analyzed BOM
- `GET /api/v1/rfq/{id}` — Get RFQ details
- `POST /api/v1/rfq/{id}/approve` — Approve RFQ
- `POST /api/v1/rfq/{id}/reject` — Reject RFQ

### Tracking
- `GET /api/v1/tracking/rfq/{id}` — Get production milestones
- `POST /api/v1/tracking/rfq/{id}/start` — Start production
- `POST /api/v1/tracking/rfq/{id}/advance` — Advance to next stage
- `POST /api/v1/tracking/rfq/{id}/feedback` — Submit execution feedback

## User Flow

1. Upload BOM (no login) → Preview analysis
2. Register / Login → Unlock full report
3. Click "Manufacture with PGI" → Create RFQ
4. Approve quote → Production tracking starts
5. Delivery → Submit feedback → System learns

## Database

Uses SQLAlchemy ORM. SQLite for dev, PostgreSQL for production.
Tables auto-created on startup. 12 tables covering BOMs, analysis, vendors, pricing, RFQs, tracking, and learning memory.

## Strategy Engine

Fully implemented decision engine that:
- Evaluates 11 global manufacturing regions
- Computes logistics, tariffs, uncertainty per candidate
- Scores with weighted multi-objective function (cost 55%, lead 20%, risk 15%, quality 10%)
- Produces recommended + alternative with cost ranges and explanations
- Learns from execution feedback via supplier memory
