# AgenticEngine V2 — Updated Phased Development Plan (V3)

## Codebase Audit Summary

Before revising the plan, here's what the current codebase **already has** and what's **missing**.

### ✅ Already Built (In-Memory, ~5,700 LOC)

| Module | LOC | What It Does | Status |
|--------|-----|-------------|--------|
| `runtime.py` | 769 | DAG pipeline executor, retries, event journal, checkpoints, conditional execution | Solid |
| `tenants.py` | 638 | Org → Workspace → Project hierarchy, RBAC, secrets vault, quotas, job queue | Solid |
| `observability.py` | 586 | Event emitter, tracer, audit log, cost metering, LLM router | Solid |
| `policy.py` | 531 | Tool policy gateway (deny-by-default), sandboxed file writer, output sanitizer | Solid |
| `connectors.py` | 651 | HTTP/MCP connectors, DLP scanning, egress policy, health checks | Solid |
| `eval.py` | 587 | Eval suites, release gates, prompt injection/tool policy test cases | Solid |
| `contracts.py` | 459 | Stage output contracts, deterministic validators, evidence tracking | Solid |
| `auth.py` | 420 | JWT validation, API key auth, middleware | Solid |
| `workers.py` | 364 | Subprocess workers, worker pool, resource quotas | Solid |
| `core.py` | 330 | OODA loop, pipeline assembly, agent definitions | Solid |
| `api.py` | 282 | REST endpoints (health, jobs CRUD, billing, audit) | Stub-level |
| Tests | 3,382 | 6 test files covering phases 1–4 + fixes | Good coverage |

### ❌ Not Built Yet

- **Zero persistence** — everything is in-memory dataclasses. No Postgres, no Redis, no SQLAlchemy.
- **Zero ArkainBrain integration** — no import, no reference anywhere.
- **Zero financial tools** — no amortization, TVM, IRR, NPV, DSCR, cap rate, sensitivity.
- **Zero EGM domain** — no data models, no ingestion, no forecasting.
- **Zero gaming contract engine** — no host agreements, no revenue share logic, no Monte Carlo.
- **Zero real estate pipeline** — no capital filter, no deal scoring, no memo export.
- **Zero portfolio layer** — no dashboards, no concentration tracking.
- **Zero ML pipeline** — no model training, no drift detection.
- **No deployment config** — no Railway, no Docker Compose for local dev, no migrations.

### Key Architectural Insight

The existing engine is a **well-designed platform kernel** — a generic agentic pipeline framework with security, policy, observability, and multi-tenancy baked in. But it's 100% in-memory and has zero domain logic. The original plan's Phase 0–2 overlap heavily with what already exists. The real work starts at persistence and domain layers.

---

## Public Data Strategy — EGM Market Intelligence

Since proprietary EGM data is not yet available, the platform will bootstrap its entire EGM data layer from **public state gaming commission data**. This is not a stopgap — these are the canonical regulatory sources that every operator uses.

### Primary Source: Illinois Gaming Board (IGB)

Illinois is the largest distributed/route gaming market in the US and provides the richest public data:

| Dataset | URL | Format | Granularity | Fields | History |
|---------|-----|--------|-------------|--------|---------|
| **Video Gaming Monthly Revenue** | `igb.illinois.gov/video-gaming/video-reports.html` | CSV | Per-establishment, monthly | Establishment name, municipality, terminal count, amount played (coin-in), amount won (coin-out), NTI (net terminal income), state/local tax | Oct 2012 → present |
| **Casino Monthly Revenue** | `igb.illinois.gov/casino-gambling/casino-reports.html` | CSV | Per-casino, monthly | EGD (slot) revenue, table revenue, AGR, admissions | 1992 → present |

**Why Illinois is ideal:**
- **~8,000+ video gaming locations** — bars, restaurants, truck stops, fraternal orgs, gaming cafés — exactly the venue types the EGM model targets
- **Per-establishment monthly granularity** — enough to train location-level prediction models
- **12+ years of history** — captures seasonal patterns, COVID impact/recovery, market maturation
- **Venue type diversity** — the data naturally segments by establishment category
- **NTI = net_win** — directly maps to the canonical `net_win = coin_in - coin_out` formula

### Secondary Sources (Cross-State Modeling)

| State | Source | Format | Granularity | Key Fields |
|-------|--------|--------|-------------|------------|
| **Nevada** | NV Gaming Control Board Monthly Revenue Reports | PDF (structured tables) | By region + denomination | Win amount, # locations, # units, win%, handle |
| **Pennsylvania** | PA Gaming Control Board Revenue Reports | Excel | Per-casino + VGT truck stops (~75 locations) | Slot revenue, table revenue, per-operator VGT revenue |
| **Colorado** | CO Division of Gaming Industry Statistics | Excel/PDF | Per-casino (3 gaming towns) | Coin-in, AGP, hold%, device counts, by denomination |
| **UNLV Center for Gaming Research** | `gaming.library.unlv.edu` | Excel | Multi-state compiled monthly | Commercial casino revenue, slot data, win/unit/day |

### Tertiary Sources (Market-Level Benchmarks)

| Source | What It Provides |
|--------|-----------------|
| **AGA Commercial Gaming Revenue Tracker** | State-by-state quarterly aggregates, slot vs. table split, YoY growth |
| **AGA State of the States Report** | Annual per-state deep dives: revenue, tax, device counts, market context |
| **State gaming commission annual reports** (IL, NV, PA, CO, MS, NJ, IN, MO, IA) | Regulatory context, market structure, licensing data |

### Data Architecture Principle

All public data flows through the same ingestion pipeline as future proprietary data. The schema is source-agnostic — a `data_source` field distinguishes public from proprietary records. When real operator data becomes available, it slots into the existing tables alongside (or replacing) the public data, with no schema changes required.

---

## Revised Plan — 10 Phases

### Guiding Principles

1. **Don't rebuild what works.** The in-memory engine is solid. Wrap it with persistence, don't rewrite it.
2. **Persistence first.** Nothing real can happen until we have a database layer under the existing in-memory models.
3. **Vertical slices > horizontal layers.** Each phase should deliver a testable, demo-able capability.
4. **ArkainBrain is an LLM orchestrator, not the platform.** It plugs into the Agent Control Plane, it doesn't replace the engine.
5. **Financial tools are foundational.** EGM, contracts, and RE pipelines all depend on the micro-tools. Build them early.
6. **Config-from-DB is non-negotiable.** Every agent config, template, and weight lives in Postgres with version history.
7. **Public data first, proprietary data later.** Bootstrap from state gaming commission data. Design the schema so proprietary data drops in without migration.

---

## PHASE 0 — Persistence Layer + Deployment Skeleton

**Objective:** Put a real database under the existing in-memory engine and get it deployable.

### 0A — Database Foundation
- SQLAlchemy models mirroring existing dataclasses: `users`, `workspaces`, `memberships`, `api_keys`, `audit_logs`
- Alembic migration framework (initial migration creates all tables)
- Repository pattern: `UserRepo`, `WorkspaceRepo`, `AuditRepo` wrapping SQLAlchemy
- Adapt existing `AuthzEngine`, `AuditLog`, `SecretsVault` to read/write via repos (keep in-memory mode for tests)

### 0B — Redis + Worker Queue
- Redis for: session cache, job queue (replace in-memory `JobQueue`), rate limiting
- Celery or `rq` worker connecting to existing `WorkerPool` / `SubprocessWorker`
- Worker heartbeat endpoint

### 0C — Deployment
- `docker-compose.yml` for local dev (API + Worker + Postgres + Redis)
- Railway config (`railway.toml`, service definitions)
- Environment config management (`.env.example`, `settings.py` via Pydantic)
- CI pipeline: lint (`ruff`) + test (`pytest`) + type-check (`mypy`)

### 0D — API Hardening
- Upgrade `api.py` from stub to real FastAPI (or Flask) app
- Wire JWT auth middleware (already written in `auth.py`) to actual endpoints
- `/health` returns DB + Redis connectivity status
- OpenAPI spec auto-generated

### Acceptance Criteria
- `docker-compose up` boots API + Worker + Postgres + Redis
- Existing test suite passes against both in-memory and DB-backed repos
- `/health` returns green with DB + Redis connected
- Worker picks up and completes a test job from Redis queue
- Alembic migrations run cleanly

---

## PHASE 1 — Agent Control Plane (ACP) in Database

**Objective:** All agent behavior is controlled via DB rows, not code. This is the brain of the system.

### 1A — Agent Config Tables
```
agent_configs (
  id, workspace_id, agent_name, version,
  model_provider, model_name, max_tokens, temperature,
  timeout_sec, retry_count, retry_backoff,
  tool_allowlist JSONB, prompt_template TEXT,
  output_schema JSONB, agent_weight FLOAT,
  enabled BOOLEAN, created_at, created_by
)
agent_config_versions (history table — append-only)
```
- API: CRUD + rollback + diff view
- On change → emit audit event (already have `AuditLog`)
- Runtime: `LLMRouter` reads config from DB at pipeline start (cache in Redis, TTL 30s)

### 1B — Model Router Config
```
model_routes (
  id, workspace_id, tier, primary_provider, primary_model,
  fallback_provider, fallback_model,
  cost_cap_per_run, latency_cap_ms, enabled
)
```
- Wire into existing `LLMRouter` + `CostMeter`

### 1C — Tool Policy in DB
```
tool_policies (
  id, workspace_id, agent_id, tool_name,
  action_scope, rate_limit_per_min, rate_limit_per_run,
  requires_approval BOOLEAN, egress_allowed_domains JSONB,
  enabled
)
```
- Wire into existing `PolicyBroker` (replace in-memory policy dicts)

### 1D — Pipeline Definitions in DB
```
pipeline_defs (
  id, workspace_id, name, version,
  stages JSONB,  -- array of {agent, dependencies, timeout, parallel}
  enabled
)
```
- Wire into existing `PipelineRuntime` + `StageDef`

### 1E — Strategy Weights
```
strategy_weights (
  id, workspace_id, version,
  mode_a_capital_filter FLOAT,
  mode_b_vertical_integration FLOAT,
  mode_c_regional_empire FLOAT,
  mode_d_opportunistic FLOAT,
  created_at, created_by
)
```
- Versioned, editable via API

### Acceptance Criteria
- Agent config changes via API take effect on next pipeline run (no deploy needed)
- Blocked tool returns 403 with audit entry
- Rollback to previous agent config version works
- Config diff view shows what changed between versions
- All config reads go through Redis cache → Postgres fallback

---

## PHASE 2 — Micro Financial Tools Suite

**Objective:** Deterministic, auditable financial calculators that every downstream pipeline depends on.

### Tools
| Tool | Inputs | Outputs |
|------|--------|---------|
| **Amortization** | principal, rate, term, extra payments | Schedule (monthly P&I breakdown), total interest |
| **TVM** | PV/FV/PMT/rate/nper (solve for any one) | Missing variable |
| **IRR / NPV** | Cash flows, discount rate (for NPV) | IRR %, NPV $ |
| **DSCR** | NOI, annual debt service | Ratio |
| **Cap Rate ⇄ NOI** | Any 2 of: cap rate, NOI, value | Missing variable |
| **Sensitivity** | Base case + variable ranges | Matrix of outcomes (2D grid) |

### Implementation
- Pure Python, zero external deps (numpy only if needed for IRR solver)
- Each tool: input Pydantic model → compute → output Pydantic model
- All tool runs saved to `tool_runs` table (inputs, outputs, user, timestamp)
- Export: CSV + PDF (via `reportlab` or similar)
- Registered in `PolicyBroker` with tool policies
- Unit tests with known-answer vectors (e.g., compare to Excel)

### Acceptance Criteria
- Every tool passes deterministic test vectors
- Tool runs persisted and queryable
- CSV + PDF export works
- Audit log captures every run

---

## PHASE 3 — EGM Data Layer + Public Data Ingestion

**Objective:** Canonical data model for EGM economics, bootstrapped entirely from public state gaming commission data.

### 3A — Core Schema
```
data_sources (
  id, name, source_type,        -- 'illinois_igb', 'nevada_gcb', 'pennsylvania_pgcb', etc.
  url, format,                   -- source URL, 'csv'/'excel'/'pdf'
  frequency,                     -- 'monthly'
  last_synced_at,
  enabled BOOLEAN
)

egm_locations (
  id, data_source_id, source_location_id,  -- original ID from the state data
  name, address, municipality, county, state,
  venue_type,                    -- 'bar', 'restaurant', 'truck_stop', 'fraternal', 'gaming_cafe', 'casino'
  lat, lng,                      -- geocoded (batch via Mapbox/Google)
  license_number,                -- state gaming license
  terminal_operator,             -- the route operator (e.g., Accel, J&J, Gold Rush)
  attributes JSONB,              -- sqft, hours, other metadata when available
  first_seen_date, last_seen_date,
  is_active BOOLEAN
)

egm_monthly_performance (
  id, location_id, data_source_id,
  report_month DATE,             -- first day of month
  terminal_count INTEGER,        -- number of active machines
  coin_in NUMERIC(14,2),         -- amount played / handle
  coin_out NUMERIC(14,2),        -- amount won by players
  net_win NUMERIC(14,2),         -- coin_in - coin_out (= NTI in Illinois)
  hold_pct NUMERIC(6,4),         -- net_win / coin_in
  tax_amount NUMERIC(12,2),      -- state + local tax (where reported)
  UNIQUE(location_id, report_month)
)

egm_machines (
  id, location_id, serial, make, model, denomination,
  install_date, removal_date, is_active
)

ingest_runs (
  id, data_source_id, workspace_id,
  run_type,                      -- 'scheduled', 'manual', 'backfill'
  period_start DATE, period_end DATE,
  status,                        -- 'pending', 'running', 'completed', 'failed'
  rows_processed INTEGER, rows_inserted INTEGER, rows_updated INTEGER, rows_errored INTEGER,
  started_at, completed_at,
  triggered_by                   -- user_id or 'scheduler'
)

ingest_errors (
  id, ingest_run_id,
  row_num INTEGER, source_column TEXT,
  error_type,                    -- 'missing_field', 'invalid_type', 'out_of_range', 'duplicate', 'parse_error'
  detail TEXT,
  raw_row JSONB                  -- original row data for debugging
)
```

**Key design decisions:**
- `data_source_id` on every record — distinguishes public from future proprietary data
- `source_location_id` — preserves the original state identifier for dedup and re-import
- `egm_monthly_performance` instead of daily — public data is monthly; schema supports daily granularity if proprietary data arrives later by adding `report_date` column
- `terminal_operator` field — critical for Illinois route gaming (Accel, J&J, Gold Rush are the major operators)

### 3B — Illinois IGB Connector (Primary)

The Illinois Gaming Board publishes monthly video gaming CSVs at `igb.illinois.gov/video-gaming/video-reports.html`.

**Connector implementation:**
```
IGB CSV Structure (expected fields):
  Municipality, Establishment, License #, # of VGTs,
  Funds In (= coin_in), Funds Out (= coin_out),
  NTI (= net_win), State Tax, Municipality Share
```

- **Scraper:** HTTP fetch of the IGB reports page → parse download links → fetch CSV for each month
- **Parser:** Column mapping from IGB field names → canonical schema
  - `Funds In` → `coin_in`
  - `Funds Out` → `coin_out`
  - `NTI` → `net_win`
  - `# of VGTs` → `terminal_count`
  - `Municipality` → `municipality`
  - `Establishment` → `name`
  - `License #` → `license_number`
- **Venue type classifier:** Heuristic + lookup from establishment name
  - Names containing "BAR", "TAP", "PUB", "SALOON" → `bar`
  - Names containing "RESTAURANT", "GRILL", "DINER", "KITCHEN", "CAFE" → `restaurant`
  - Names containing "TRUCK", "TRAVEL", "FUEL", "GAS" → `truck_stop`
  - Names containing "LEGION", "VFW", "MOOSE", "ELKS", "KNIGHTS" → `fraternal`
  - Names containing "GAMING", "SLOTS", "AMUSEMENT" → `gaming_cafe`
  - Default → `other` (manual review queue)
- **Geocoding:** Batch geocode `name + municipality + ", IL"` via Mapbox/Google (rate-limited, cached)
- **Idempotent:** Upsert on `(source_location_id, report_month)` — re-running a month replaces, doesn't duplicate
- **Backfill:** One-time job to ingest all historical CSVs from Oct 2012 → present (~150 monthly files)

### 3C — Secondary Source Connectors

**Nevada Gaming Control Board:**
- Source: Monthly Revenue Report PDFs from `gaming.nv.gov`
- Parser: PDF table extraction (tabula-py or camelot) → structured data
- Granularity: By region (Las Vegas Strip, Downtown, Boulder Strip, Reno/Sparks, etc.) + denomination
- Fields: # locations, # units, win amount ($000s), win %, YoY change
- Limitation: Aggregated by region, not per-location — useful for market benchmarks, not location-level prediction
- Maps to `egm_monthly_performance` with a synthetic `egm_location` per region

**Pennsylvania Gaming Control Board:**
- Source: Revenue Excel reports from `gamingcontrolboard.pa.gov/news-and-transparency/revenue`
- Parser: openpyxl → canonical schema
- Granularity: Per-casino (17 casinos) + VGT truck stops (~75 locations, per-operator)
- Fields: Slot revenue, table revenue, VGT revenue by operator
- Maps to `egm_monthly_performance` — casinos as individual locations, VGTs aggregated by operator

**Colorado Division of Gaming:**
- Source: Industry Statistics Excel from `sbg.colorado.gov/industry-statistics-gaming`
- Parser: openpyxl → canonical schema
- Granularity: Per-casino (Black Hawk, Central City, Cripple Creek)
- Fields: Coin-in, AGP (= net_win), hold%, device counts, by denomination
- Maps directly to canonical schema — Colorado uses the exact same terminology

**UNLV Center for Gaming Research:**
- Source: Compiled Excel spreadsheets from `gaming.library.unlv.edu`
- Parser: openpyxl → canonical schema
- Granularity: Multi-state monthly (commercial casino revenue)
- Fields: Monthly win, win/unit/day, device counts
- Use as validation/enrichment layer, not primary source

### 3D — Ingestion Pipeline Architecture

```
Scheduler (monthly cron or manual trigger)
    │
    ▼
Source Connector (per data_source)
    │  - Fetch raw file (CSV/Excel/PDF)
    │  - Store raw file in blob storage (audit trail)
    │
    ▼
Parser (source-specific)
    │  - Column mapping → canonical schema
    │  - Type coercion + validation
    │  - Venue type classification (IL only)
    │  - Derived field computation (hold_pct, net_win if not provided)
    │
    ▼
Loader
    │  - Upsert to egm_monthly_performance
    │  - Upsert to egm_locations (new locations auto-created)
    │  - Write ingest_errors for bad rows
    │  - Update ingest_runs with final counts
    │
    ▼
Post-Ingest
    │  - Geocoding queue (new/ungeocoded locations)
    │  - Data health checks (trigger Phase 3E)
    │  - Audit log entry
```

- Each source connector implements a common `DataSourceConnector` interface
- Raw files stored before parsing (reproducibility)
- Entire pipeline is idempotent — safe to re-run any month
- Runs as a Celery task (from Phase 0B worker queue)

### 3E — Data Health Dashboard (API)

**Completeness checks:**
- Missing months per location (gap detection)
- Locations that stopped reporting (churn detection)
- New locations appearing (market growth tracking)

**Anomaly detection:**
- Coin-in Z-score > 3 vs. location's rolling 12-month average
- Hold% deviation > 2σ from venue-type average for that state
- Terminal count changes (machine adds/removes)
- NTI sign flip (location going from profitable to unprofitable)

**Market aggregations:**
- Performance by state (IL, NV, PA, CO)
- Performance by venue type (bar, restaurant, truck stop, fraternal, casino)
- Performance by terminal operator (Accel, J&J, Gold Rush, etc. — IL only)
- Monthly/quarterly/annual trends with YoY comparison
- Top/bottom performers by net_win, hold%, growth rate

**Endpoints:**
```
GET /egm/health                              → overall data quality summary
GET /egm/health/source/{source_id}           → per-source health
GET /egm/performance?state=IL&venue_type=bar → filtered aggregations
GET /egm/locations/{id}/history              → time series for one location
GET /egm/trends?metric=net_win&group_by=state → trend analysis
GET /egm/ingest/runs                         → ingest job history
GET /egm/ingest/runs/{id}/errors             → errors for a specific run
```

### 3F — Data Volume Estimates

| Source | Locations | Months Available | Total Rows (est.) |
|--------|-----------|------------------|-------------------|
| Illinois IGB (video gaming) | ~8,000 | ~150 (Oct 2012→present) | ~1,200,000 |
| Illinois IGB (casinos) | ~15 | ~380 (1992→present) | ~5,700 |
| Nevada GCB | ~30 regions | ~300 (2000→present) | ~9,000 |
| Pennsylvania PGCB | ~92 (17 casinos + 75 VGTs) | ~150 (2012→present) | ~13,800 |
| Colorado DOG | ~40 casinos | ~300 (2000→present) | ~12,000 |
| **Total** | **~8,177** | | **~1,240,500** |

Illinois alone provides >95% of the location-level training data. The other states add market-level context and cross-state validation.

### Acceptance Criteria
- Illinois IGB backfill completes: all months from Oct 2012 → present ingested
- Secondary sources (NV, PA, CO) ingested with at least 3 years of history
- Re-running an ingest for the same month produces no duplicates
- Derived metrics (net_win, hold_pct) computed correctly on every record
- Venue type classification covers >85% of Illinois locations (remainder in review queue)
- Geocoding completes for >90% of locations
- Data health endpoints return meaningful alerts for gaps and anomalies
- All ingest runs logged in audit trail with error details

---

## PHASE 4 — EGM Location Analyzer (Forecast Engine)

**Objective:** Predict coin-in, hold%, and net-win for a given location with confidence bands, trained on public data.

### 4A — Feature Engineering

**Features derived from Illinois IGB data:**
- **Location features:** state, municipality, venue_type, terminal_count
- **Geocoded features:** lat/lng, population density (Census), median household income (ACS), competitor count within 5mi/10mi radius
- **Historical performance features:** (only for existing locations or same-venue-type averages)
  - Mean/median coin-in by venue_type + state
  - Seasonal indices (month-of-year effect, derived from 12 years of IL data)
  - Market maturation curve (IL video gaming grew exponentially 2012–2018, then stabilized)
  - Terminal operator performance (some operators consistently outperform others in IL)
- **Market density features:**
  - Number of gaming locations within municipality
  - Locations per capita in municipality
  - Saturation index (total terminals / population)

**Features derived from secondary sources:**
- State-level hold% benchmarks (NV, PA, CO provide hold% context)
- Win-per-unit-per-day benchmarks by venue type
- Regional gaming market growth rates

**Feature store:**
```
location_features (
  id, location_id, feature_version,
  features JSONB,               -- complete feature vector
  computed_at TIMESTAMP
)
```

### 4B — Model Training

**Training data:** Illinois IGB monthly performance (1.2M+ rows across 8,000+ locations)

**Models:**
- **Coin-in prediction:** Quantile regression (XGBoost/LightGBM) for p10/p50/p90
  - Train on: venue_type, terminal_count, municipality population, income, competitor density, seasonal index, market maturation
  - Target: monthly coin_in
- **Hold% prediction:** Separate model (different drivers — denomination mix, venue type, state regulation)
  - Train on: venue_type, state, terminal_count, market maturity
  - Target: hold_pct
- **Net-win derivation:** coin_in × hold_pct (computed from the two model outputs, not a separate model)

**Validation:**
- Train/test split: train on 2012–2023, validate on 2024, test on 2025
- Cross-validation within training set (5-fold, stratified by venue_type)
- Calibration check: does the p10–p90 band actually contain 80% of actuals?
- Backtest: for each location, predict month N from months 1..N-1

**Model registry:**
```
model_registry (
  id, model_name, version, model_type,  -- 'coin_in_quantile', 'hold_pct_quantile'
  training_data_range,                   -- '2012-10 to 2023-12'
  metrics JSONB,                         -- MAE, MAPE, calibration, feature importance
  artifact_path TEXT,                    -- path to serialized model
  is_champion BOOLEAN,
  promoted_at, promoted_by,
  created_at
)
```

### 4C — Confidence Score

Confidence is a composite of:
- **Feature completeness:** what % of the feature vector is populated vs. imputed
- **Similar location count:** how many locations in the training set match this venue_type + state + population band
- **Prediction interval width:** narrower bands = higher confidence
- **Data recency:** models trained on stale data get penalized

Score range: 0.0–1.0, bucketed as LOW (<0.4), MEDIUM (0.4–0.7), HIGH (>0.7)

### 4D — Prediction API
```
POST /egm/predict
{
  "address": "123 Main St, Springfield, IL",
  "venue_type": "bar",
  "terminal_count": 5,
  "attributes": { "sqft": 2000, "hours": "6am-2am" }
}
→ {
  "coin_in":  { "p10": 45000, "p50": 78000, "p90": 125000 },
  "hold_pct": { "p10": 0.22, "p50": 0.26, "p90": 0.31 },
  "net_win":  { "p10": 9900, "p50": 20280, "p90": 38750 },
  "confidence": 0.78,
  "confidence_level": "HIGH",
  "similar_locations": [
    { "name": "Joe's Bar & Grill", "municipality": "Springfield", "monthly_net_win_avg": 19500 },
    { "name": "The Tap House", "municipality": "Chatham", "monthly_net_win_avg": 22100 }
  ],
  "model_version": "coin_in_v3.2 / hold_pct_v2.1",
  "data_note": "Prediction based on Illinois Gaming Board public data (8,000+ locations, 2012-present)"
}
```

### 4E — Map Integration
- Mapbox GL JS frontend support
- Address geocoding (Mapbox or Google)
- Venue type selector + attributes panel (API-driven)
- Prediction overlay on map (color-coded by net_win potential)
- Existing location markers with performance history on hover
- Heat map layer showing market density/saturation

### Acceptance Criteria
- Forecast bands are statistically calibrated (backtest: p50 within ±20% on held-out 2024–2025 data)
- Model beats naive baseline (venue-type average) by >15% on MAE
- Every prediction stored with model version + inputs (auditable)
- Confidence score reflects actual prediction quality (high confidence predictions are more accurate)
- Similar locations returned for human sanity-check
- Prediction latency < 500ms (features pre-computed, model in memory)

---

## PHASE 5 — Contract Engine + Structure Optimizer

**Objective:** Model gaming contracts, optimize deal structures, and compute negotiation guardrails.

### 5A — Contract Architecture
**Layer 1 — Host Agreement Types:**
- Revenue share (operator % / host %)
- Flat lease (monthly fixed payment to host)
- Hybrid (base lease + revenue share above threshold)

**Layer 2 — Acquisition Types:**
- Cash purchase
- Financed purchase (loan terms feed into amortization tool from Phase 2)

### 5B — Template System
```
contract_templates (
  id, workspace_id, name, version,
  agreement_type, acquisition_type,
  terms_schema JSONB,    -- defines allowed fields + constraints
  state_applicability TEXT[],
  constraints JSONB,     -- min/max bounds
  approval_required BOOLEAN
)
```
- ACP-managed: versioned, state-specific, constraint-validated
- Approval workflow for template changes

### 5C — Custom Overrides
```
contract_overrides (
  id, deal_id, run_id, template_id,
  overrides JSONB,   -- only the fields that differ from template
  schema_valid BOOLEAN,
  approval_status, approved_by, approved_at
)
```
- Schema-validated against template's `terms_schema`
- Optional approval gate (HITL via existing `PolicyBroker`)
- Can promote override → new template version

### 5D — Monte Carlo Engine

**Inputs fed from public data models (Phase 4):**
- Coin-in distribution → from Illinois IGB-trained quantile model (p10/p50/p90)
- Hold% distribution → from state-level hold% model
- Contract terms → from template (Phase 5B)
- Acquisition cost → user input or market estimate

**Simulation (default 10,000 scenarios):**
- Sample coin-in from predicted distribution
- Sample hold% from predicted distribution
- Apply contract logic (rev share / lease / hybrid) to each scenario
- Compute operator monthly cash flows
- Feed into IRR calculator (Phase 2)

**Outputs:**
- IRR distribution (p10/p25/p50/p75/p90)
- Downside risk metrics (probability of IRR < threshold, max drawdown)
- Ranked structures (which contract type wins in which scenarios)
- Negotiation guardrails: max lease, min split, guarantee bounds
- Machine count recommendation (optimize for target IRR)

### Acceptance Criteria
- Templates + overrides CRUD works with validation
- Monte Carlo produces stable results (rerun variance < 1% at 10K sims)
- Financial math ties out to Phase 2 tools
- Ranked recommendations make economic sense
- Guardrails computed and stored

---

## PHASE 6 — Real Estate Capital Filter Pipeline

**Objective:** End-to-end deal evaluation pipeline for real estate acquisitions.

### 6A — Pipeline Stages (wired via ACP)
1. **Intake** — property data collection + validation
2. **Feasibility** — zoning, environmental, preliminary screen
3. **Market** — comp analysis, rent/sale trends, demand drivers
4. **Cost** — renovation/build estimates, capex schedule
5. **Finance** — capital structure, debt terms, equity requirements
6. **Risk** — Monte Carlo simulation (reuse Phase 5 engine with RE parameters)
7. **Decision Scoring** — weighted score → GO / HOLD / NO-GO

### 6B — Property Templates
- Retail strip, QSR, Gas station, Dollar-style, Shopping center
- Each template defines: default assumptions, stage-specific parameters, scoring weights
- Stored in `pipeline_templates` table (ACP-managed)

### 6C — Outputs
- IRR, DSCR, risk score computed per deal
- Strategy weights (from Phase 1E) applied to scoring
- Decision: GO / HOLD / NO-GO with explanation
- Memo export (PDF) with all stage outputs, assumptions, risk analysis

### 6D — Integration Points
- Financial tools (Phase 2) for all calculations
- EGM forecaster (Phase 4) when property has gaming component — "What would 5 VGTs generate at this gas station?"
- Contract engine (Phase 5) when deal includes gaming contracts
- Portfolio (Phase 7) for concentration impact
- Public data context: "This municipality has 47 licensed gaming locations, avg NTI $18K/mo"

### Acceptance Criteria
- End-to-end deal run produces scored recommendation
- Strategy weights shift the decision boundary
- All runs stored, auditable, exportable as PDF memo

---

## PHASE 7 — Portfolio Brain

**Objective:** Portfolio-level visibility, concentration tracking, and new-deal impact analysis.

### 7A — Portfolio Data Model
```
portfolio_assets     (id, workspace_id, name, type, location, acquisition_date, current_value)
portfolio_debt       (id, asset_id, lender, balance, rate, maturity_date, payment_schedule)
portfolio_noi        (id, asset_id, period, noi_amount)
portfolio_egm_exposure (id, asset_id, location_id, machine_count, monthly_net_win)
```

### 7B — Dashboard APIs
- State exposure (% of portfolio by state)
- Venue type exposure
- Contract type exposure
- Ownership vs. financed split
- Debt maturity ladder (by quarter/year)
- Concentration heat maps (Herfindahl index by dimension)
- EGM market context: overlay portfolio locations against public data market density

### 7C — New Deal Impact
- When a deal run completes (Phase 6), compute:
  - Concentration delta (how does this deal shift exposure?)
  - Leverage shift (debt-to-equity before/after)
  - Risk shift (portfolio-level risk score change)
  - Market saturation check (using public data: how many competitors in this municipality?)
- Surface as warnings/recommendations in deal output

### Acceptance Criteria
- Dashboard APIs return correct aggregations
- New deal impact computed and shown alongside deal recommendation
- Recommendations adjust dynamically as portfolio changes

---

## PHASE 8 — ArkainBrain Integration

**Objective:** Wire ArkainBrain as the LLM orchestration layer within the Agent Control Plane.

> **Note:** This was Phase 0 in the original plan, but moved here intentionally. The platform must be stable, the data models must exist, and the financial tools must work before we let an LLM orchestrator drive them. ArkainBrain needs something to orchestrate.

### 8A — ArkainBrain Runtime Adapter
- Import ArkainBrain as a library
- Adapter class: `ArkainBrainAdapter` implementing the engine's `LLMRouter` interface
- Config read from DB (Phase 1A agent configs)
- All calls go through `PolicyBroker` + `CostMeter`

### 8B — Agent Wiring
- Map ArkainBrain agents to engine `AgentDefinition`
- Tool registration: financial tools (Phase 2), EGM tools (Phase 3–4), contract tools (Phase 5)
- HITL approval hooks wired to existing `PolicyBroker.requires_approval`

### 8C — Pipeline Orchestration
- ArkainBrain executes pipelines defined in ACP (Phase 1D)
- Each stage: ArkainBrain agent → tool calls → contract validation → next stage
- Existing `PipelineRuntime` handles DAG, retries, checkpoints
- ArkainBrain handles LLM reasoning, tool selection, output formatting

### Acceptance Criteria
- ArkainBrain reads all config from DB (zero hardcoded models)
- Pipeline runs end-to-end with ArkainBrain agents
- Tool calls pass policy gateway
- Cost tracked per run
- HITL approval blocks execution until approved

---

## PHASE 9 — Continuous Learning + Experiment Mode

**Objective:** Keep models accurate, detect drift, and safely experiment with config changes.

### 9A — Model Retraining Pipeline
- Monthly retrain triggered after new IGB/state data ingested (replaces arbitrary weekly schedule)
- Champion/Challenger framework: new model must beat current on held-out set
- Approval gate: no auto-promotion without human sign-off
- Model registry tracks all versions + metrics

### 9B — Drift Detection
- **Prediction vs. actuals:** as new monthly public data arrives, compare last month's predictions to actual NTI
- **State-level drift:** alert if Illinois model accuracy degrades (e.g., due to regulatory change)
- **Venue-type drift:** alert if a venue category's error rate spikes (e.g., fraternal orgs declining industry-wide)
- **Market shift detection:** alert if a municipality's total gaming revenue changes >20% YoY (new competitor, population shift)
- Alerts → audit log + configurable notification (email/Slack webhook)

### 9C — Experiment Mode (ACP)
- Clone an agent config set → "experiment" variant
- Batch test: run N historical deals through both champion and challenger configs
- Compare: cost, latency, accuracy, recommendation quality
- Promote with approval: if challenger wins, promote with HITL sign-off
- Rollback: instant revert to previous config version

### 9D — Data Expansion Pathway
- When proprietary operator data becomes available:
  - Ingest via the same pipeline (new `data_source` record)
  - Retrain models with combined public + proprietary data
  - Proprietary data unlocks daily granularity, per-machine performance, denomination mix
  - Champion/Challenger ensures new models with richer data actually perform better before promotion

### Acceptance Criteria
- No model auto-promotion without human approval
- Drift alerts fire within 24h of new data ingest showing threshold breach
- A/B experiments run safely (experiment never affects production)
- Full audit trail of all promotions and rollbacks
- Retraining triggered automatically when new monthly data ingested

---

## Non-Negotiable Platform Rules (Unchanged)

1. ArkainBrain (and all LLM calls) read config from DB at runtime
2. No hardcoded models, temperatures, or prompts in application code
3. No hardcoded contract logic — all terms come from templates
4. All configs versioned with diff history
5. All overrides logged in audit trail
6. Tool calls must pass policy gateway (deny-by-default)
7. HITL approvals fail-closed (if approval system is down, block the action)
8. Platform hard caps (cost, token, rate) enforced globally via `QuotaEnforcer`

---

## Execution Order

```
Phase 0  ██████████  Persistence + Deploy       (Foundation — everything depends on this)
Phase 1  ██████████  ACP in Database             (Control plane — everything reads config from here)
Phase 2  ████████    Micro Financial Tools       (Shared dependency for Phases 4–7)
Phase 3  ██████████  EGM Data + Public Ingest    (Domain data — IL/NV/PA/CO public sources)
Phase 4  ████████    EGM Forecaster              (ML on public data — feeds Phase 5)
Phase 5  ██████      Contract Engine             (Business logic — feeds Phase 6)
Phase 6  ██████      RE Capital Filter           (Pipeline — uses everything)
Phase 7  ████        Portfolio Brain             (Aggregation layer)
Phase 8  ████        ArkainBrain Integration     (LLM orchestration over working platform)
Phase 9  ████        Continuous Learning          (Operational maturity + data expansion)
```

### Parallelism Opportunities
- Phase 2 (financial tools) can start as soon as Phase 0B (DB) is done — doesn't need full ACP
- Phase 3 (EGM data + public ingest) can partially overlap with Phase 2 (different focus)
- Phase 3B (Illinois connector) should start early — the backfill takes time and ML training (Phase 4) needs the data
- Phase 7 (portfolio) can start schema work during Phase 6
- Phase 8 (ArkainBrain) adapter work can start during Phase 5 if interfaces are stable

---

## Key Changes from Original Plan

| Original | Updated | Rationale |
|----------|---------|-----------|
| Phase 0: ArkainBrain first | Phase 8: ArkainBrain later | Nothing to orchestrate yet. Build the platform and domain logic first. |
| Phase 0: Initialize monorepo | Removed | Already done — codebase exists with 5.7K LOC. |
| Phase 1: Auth from scratch | Phase 0A: Persist existing auth | Auth module already exists, just needs DB backing. |
| Phase 2: ACP as new build | Phase 1: ACP in DB | Existing engine already has the runtime concepts — just need DB config layer. |
| Phases 0–2 overlap with existing code | Phases 0–1 wrap existing code with persistence | Don't rebuild. Persist and extend. |
| No explicit persistence phase | Phase 0 is entirely persistence | This was the biggest gap. |
| Financial tools in Phase 3 | Financial tools in Phase 2 | They're a shared dependency for everything downstream. Build early. |
| EGM: "bring your own data" | EGM: public data first (IL, NV, PA, CO) | No proprietary data available. Illinois IGB provides 1.2M+ location-month records — enough to train real models. |
| CSV upload wizard only | Automated connectors + upload wizard | Public sources need scrapers/parsers; manual upload preserved for future proprietary data. |
| Daily performance table | Monthly performance table (daily-ready) | Public data is monthly. Schema designed so daily column can be added when proprietary data arrives. |
| Weekly model retrain | Monthly retrain after data ingest | Aligns with the monthly cadence of public data releases. |
| No data source tracking | `data_source_id` on every record | Essential for distinguishing public vs. proprietary data and for audit trail. |
