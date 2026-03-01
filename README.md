# AgenticEngine V2

Enterprise-grade agentic AI pipeline engine for real estate investment and gaming contract analysis.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Arkain Web (Next.js 14)                 │
│  3-pane layout · Intake Wizard · Streaming Chat      │
│  Artifacts · Admin · Mock API / FastAPI switchable    │
├─────────────────────────────────────────────────────┤
│                   API Server (FastAPI)               │
│              75 endpoints · JWT auth · RBAC          │
├──────────┬──────────┬───────────┬───────────────────┤
│ Contracts│ EGM/VGT  │ Real Est. │ Portfolio Brain   │
│ Monte    │ Forecast │ 7-Stage   │ Concentration     │
│ Carlo    │ + Ingest │ Pipeline  │ + Deal Impact     │
├──────────┴──────────┴───────────┴───────────────────┤
│           Agent Control Plane (ACP)                  │
│  DB-backed agent configs · model routes · policies   │
├─────────────────────────────────────────────────────┤
│        Runtime: DAG · Retry · Checkpointing          │
│        Policy: deny-by-default · audit trail         │
│        Observability: cost tracking · tracing         │
├─────────────────────────────────────────────────────┤
│    PostgreSQL          Redis          Alembic        │
└─────────────────────────────────────────────────────┘
```

## Quick Start

### Docker Compose (recommended)

```bash
cp .env.example .env        # Edit secrets for production
docker-compose up -d         # Starts Postgres + Redis + API + Worker + Web
# API:  http://localhost:8000/health
# Web:  http://localhost:3000
```

### Local Development

```bash
# 1. Backend — Install (editable with dev tools)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Start infrastructure
docker-compose up -d db redis

# 3. Run migrations
alembic upgrade head

# 4. Start API server
uvicorn engine.api_server:app --reload --port 8000

# 5. Frontend — Install & start
cd web && npm install && npm run dev    # http://localhost:3000

# 6. Run backend tests
cd .. && pytest
```

### CLI

```bash
agentic-engine serve                # Start API server
agentic-engine migrate              # Run Alembic migrations
agentic-engine seed                 # Seed default templates
agentic-engine check                # Health check
```

## Environment Variables

Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | `development` / `staging` / `production` |
| `DATABASE_URL` | — | Full Postgres URL (overrides DB_HOST/etc) |
| `DB_HOST` | `localhost` | Postgres host |
| `REDIS_URL` | — | Full Redis URL (overrides REDIS_HOST/etc) |
| `JWT_SECRET` | — | **Must set in production** |
| `SECRETS_MASTER_KEY` | — | **Must set in production** |
| `CORS_ORIGINS` | `*` (dev) | Comma-separated allowed origins |
| `RATE_LIMIT_PER_MIN` | `100` | Default API rate limit |

## API Endpoints (75 total)

### Core
- `GET /health` — Health check
- `POST /api/v1/jobs` — Submit pipeline job
- `GET /api/v1/jobs` — List jobs

### Agent Control Plane
- `GET /api/v1/agents` — List agent configs
- `GET /api/v1/model-routes` — List model routes
- `GET /api/v1/tool-policies` — List tool policies
- `GET /api/v1/strategy-weights` — Get strategy weights

### Financial Tools
- `POST /api/v1/tools/execute` — Run a financial tool
- `POST /api/v1/tools/batch` — Batch tool execution

### EGM / Gaming
- `POST /api/v1/egm/ingest` — Ingest public gaming data
- `POST /api/v1/egm/train` — Train EGM forecasting model
- `POST /api/v1/egm/predict` — Predict gaming revenue
- `GET /api/v1/egm/locations` — List gaming locations
- `GET /api/v1/egm/health` — Market health stats

### Contracts
- `POST /api/v1/contracts/analyze` — Monte Carlo simulation
- `POST /api/v1/contracts/compare` — Compare contract structures
- `GET /api/v1/contracts/templates` — List contract templates

### Real Estate
- `POST /api/v1/deals/evaluate` — 7-stage deal evaluation
- `POST /api/v1/deals/evaluate-with-gaming` — Deal + gaming integration
- `GET /api/v1/deals` — List deal runs

### Portfolio
- `GET /api/v1/portfolio/dashboard` — Portfolio dashboard
- `POST /api/v1/portfolio/deal-impact` — New deal impact analysis
- `POST /api/v1/portfolio/assets` — Add portfolio asset

### Brain (Agent Orchestration)
- `POST /api/v1/brain/run` — Execute an agent on a task
- `POST /api/v1/brain/pipeline` — Run multi-stage pipeline

### Learning
- `POST /api/v1/learning/retrain` — Trigger model retraining
- `POST /api/v1/learning/drift-check` — Check for model drift

## Project Structure

```
engine/
├── api_server.py        # FastAPI server (75 endpoints)
├── cli.py               # CLI entrypoint
├── auth.py              # JWT + API key auth
├── core.py              # AgentDefinition, Pipeline config
├── runtime.py           # DAG runtime, retry, checkpointing
├── policy.py            # Deny-by-default PolicyBroker
├── observability.py     # CostMeter, Tracer, AuditLog
├── acp.py               # Agent Control Plane (DB-backed)
├── tenants.py           # Multi-tenant RBAC, quotas
├── db/
│   ├── models.py        # All SQLAlchemy models (28 tables)
│   ├── session.py       # DB session management
│   ├── settings.py      # Environment config
│   ├── repositories.py  # Core repos
│   └── migrations/      # Alembic migrations
├── financial/
│   └── tools.py         # Amortization, IRR, DSCR, cap rate
├── egm/
│   ├── pipeline.py      # Data ingestion pipeline
│   ├── features.py      # Feature engineering
│   ├── prediction.py    # ML forecasting service
│   └── analytics.py     # Market health analytics
├── contracts/
│   ├── templates.py     # Gaming contract templates
│   ├── montecarlo.py    # Monte Carlo simulation engine
│   ├── analyzer.py      # Deal analyzer
│   └── validation.py    # Contract validation
├── realestate/
│   ├── pipeline.py      # 7-stage deal evaluation
│   ├── stages.py        # Pure function pipeline stages
│   └── templates.py     # Property templates
├── portfolio/
│   └── analytics.py     # Dashboard, HHI, deal impact
└── brain/
    ├── adapter.py       # ArkainBrain adapter
    ├── tools.py         # Unified tool registry
    └── learning.py      # Drift detection, experiments
tests/
├── test_phase0a.py      # Persistence + session
├── test_phase1_acp.py   # Agent Control Plane
├── test_phase2_fin.py   # Financial tools
├── test_phase3_egm.py   # EGM data pipeline
├── test_phase4.py       # Core engine + forecaster
├── test_phase5_contracts.py  # Contract engine
├── test_phase6_realestate.py # RE capital filter
├── test_phase7_portfolio.py  # Portfolio brain
└── test_phase89_brain.py     # Agent orchestration + learning
web/                     # ── Next.js 14 Frontend ──
├── src/
│   ├── app/
│   │   ├── page.tsx             # Root → AppShell
│   │   ├── layout.tsx           # HTML layout + metadata
│   │   ├── globals.css          # CSS vars, dark/light themes
│   │   └── api/mock/            # Mock API routes (NDJSON stream)
│   │       ├── threads/route.ts
│   │       ├── artifacts/route.ts
│   │       ├── stream/route.ts
│   │       └── admin/route.ts
│   ├── components/
│   │   ├── layout/
│   │   │   ├── app-shell.tsx    # 3-pane orchestrator
│   │   │   └── left-panel.tsx   # Threads, admin nav, theme
│   │   ├── chat/
│   │   │   ├── chat-panel.tsx   # Message list + streaming
│   │   │   ├── composer.tsx     # Input + send button
│   │   │   ├── message-bubble.tsx
│   │   │   ├── tool-card.tsx    # Expandable tool I/O
│   │   │   └── markdown.tsx     # Lightweight MD renderer
│   │   ├── intake/
│   │   │   ├── intake-modal.tsx # 6-step deal wizard
│   │   │   └── field-group.tsx  # Dynamic form fields
│   │   ├── artifacts/
│   │   │   └── artifact-panel.tsx # Right panel, pin/search
│   │   └── admin/
│   │       └── admin-panel.tsx  # Models, policies, audit
│   ├── hooks/
│   │   └── use-stream.ts       # Streaming state machine
│   └── lib/
│       ├── contracts.ts         # API type contracts (shared)
│       ├── constants.ts         # Property types, field defs
│       ├── streaming.ts         # NDJSON parser + scenario builder
│       ├── api-client.ts        # Typed fetch wrapper
│       └── cn.ts                # clsx + tailwind-merge
├── tailwind.config.ts
├── next.config.ts               # output: standalone + rewrites
├── Dockerfile                   # Multi-stage Node 20 build
└── .env.local                   # NEXT_PUBLIC_API_MODE=mock
```

## Testing

```bash
pytest                          # All tests
pytest tests/test_phase5_contracts.py -v   # Single phase
pytest -k "test_monte_carlo"    # By pattern
pytest --cov=engine             # Coverage report
```

## License

MIT
