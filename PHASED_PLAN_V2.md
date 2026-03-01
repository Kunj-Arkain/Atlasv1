# AgenticEngine V2 — Updated Phased Development Plan

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

## Revised Plan — 10 Phases

### Guiding Principles for the Revision

1. **Don't rebuild what works.** The in-memory engine is solid. Wrap it with persistence, don't rewrite it.
2. **Persistence first.** Nothing real can happen until we have a database layer under the existing in-memory models.
3. **Vertical slices > horizontal layers.** Each phase should deliver a testable, demo-able capability.
4. **ArkainBrain is an LLM orchestrator, not the platform.** It plugs into the Agent Control Plane, it doesn't replace the engine.
5. **Financial tools are foundational.** EGM, contracts, and RE pipelines all depend on the micro-tools. Build them early.
6. **Config-from-DB is non-negotiable.** Every agent config, template, and weight lives in Postgres with version history.

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

## PHASE 3 — EGM Data Layer

**Objective:** Canonical data model for EGM (Electronic Gaming Machine) economics with robust ingestion.

### 3A — Schema
```
egm_locations    (id, name, address, state, venue_type, lat, lng, attributes JSONB)
egm_hosts        (id, location_id, name, contact JSONB)
egm_machines     (id, location_id, host_id, serial, make, model, install_date)
egm_contracts    (id, location_id, host_id, contract_type, terms JSONB, effective_date, expiry_date)
egm_daily_performance (
  id, machine_id, location_id, report_date,
  coin_in, coin_out,
  -- derived (computed on insert via trigger or app-layer):
  net_win,    -- coin_in - coin_out
  hold_pct    -- net_win / coin_in
)
ingest_jobs      (id, workspace_id, filename, status, row_count, error_count, started_at, completed_at)
ingest_errors    (id, ingest_job_id, row_num, column, error_type, detail)
```

### 3B — Ingestion Wizard
- CSV upload endpoint
- Column mapping UI support (API returns detected columns, user confirms mapping)
- Validation: required fields, data types, date formats, range checks
- Idempotent: upsert on (machine_id, report_date) composite key
- Error reporting: per-row errors stored, summary returned

### 3C — Data Health Dashboard (API)
- Missing days detection (gaps in daily performance)
- Outlier detection (coin_in Z-score > 3)
- Hold% anomaly alerts (deviation from rolling 30-day average)
- Performance aggregation by state, venue type, time period
- Endpoints: `GET /egm/health`, `GET /egm/performance`

### Acceptance Criteria
- CSV ingestion is repeatable (re-upload same file = no duplicates)
- Derived metrics auto-calculated on insert
- Data health endpoints return meaningful alerts
- All ingestion jobs audited

---

## PHASE 4 — EGM Location Analyzer (Forecast Engine)

**Objective:** Predict coin-in, hold%, and net-win for a given location with confidence bands.

### 4A — Feature Engineering
- Location attributes: state, venue type, population density, competitor proximity, demographics
- Historical aggregates: avg coin-in by venue type/state, seasonal patterns, trend
- Compute feature vectors, store in `location_features` table

### 4B — Model Training
- Baseline: XGBoost or LightGBM for coin-in prediction (p10/p50/p90 via quantile regression)
- Hold% prediction: separate model (different drivers)
- Net-win derived from coin-in × hold% distributions
- Model artifacts stored in `model_registry` table (versioned, with metrics)
- Confidence score: based on feature completeness + similar-location count

### 4C — Prediction API
```
POST /egm/predict
{
  "address": "...",
  "venue_type": "bar",
  "attributes": { "sqft": 2000, "hours": "6am-2am" }
}
→ {
  "coin_in":  { "p10": 800, "p50": 1200, "p90": 1800 },
  "hold_pct": { "p10": 0.06, "p50": 0.08, "p90": 0.11 },
  "net_win":  { "p10": 48, "p50": 96, "p90": 198 },
  "confidence": 0.82,
  "similar_locations": [ ... ],
  "model_version": "v3.2"
}
```

### 4D — Map Integration
- Mapbox GL JS frontend support
- Address geocoding (Mapbox or Google)
- Venue type selector + attributes panel (API-driven)
- Prediction overlay on map

### Acceptance Criteria
- Forecast bands are statistically calibrated (backtest: p50 within ±15% on held-out data)
- Every prediction stored with model version + inputs (auditable)
- Confidence score reflects data quality
- Similar locations returned for human sanity-check

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
- Inputs: coin-in distribution (from Phase 4), hold% distribution, contract terms, acquisition cost
- Simulate N scenarios (default 10,000):
  - Apply contract logic (rev share / lease / hybrid) to each scenario
  - Compute operator cash flows
  - Feed into IRR calculator (Phase 2)
- Outputs:
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
- EGM forecaster (Phase 4) when property has gaming component
- Contract engine (Phase 5) when deal includes gaming contracts
- Portfolio (Phase 7) for concentration impact

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

### 7C — New Deal Impact
- When a deal run completes (Phase 6), compute:
  - Concentration delta (how does this deal shift exposure?)
  - Leverage shift (debt-to-equity before/after)
  - Risk shift (portfolio-level risk score change)
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
- Weekly scheduled retrain of EGM forecasting models (Phase 4)
- Champion/Challenger framework: new model must beat current on held-out set
- Approval gate: no auto-promotion without human sign-off
- Model registry tracks all versions + metrics

### 9B — Drift Detection
- Accuracy tracking: compare predictions to actuals as new daily data arrives
- State-level drift: alert if a specific state's predictions degrade
- Venue-type drift: alert if a venue category's error rate spikes
- Alerts → audit log + configurable notification (email/Slack webhook)

### 9C — Experiment Mode (ACP)
- Clone an agent config set → "experiment" variant
- Batch test: run N historical deals through both champion and challenger configs
- Compare: cost, latency, accuracy, recommendation quality
- Promote with approval: if challenger wins, promote with HITL sign-off
- Rollback: instant revert to previous config version

### Acceptance Criteria
- No model auto-promotion without human approval
- Drift alerts fire within 24h of threshold breach
- A/B experiments run safely (experiment never affects production)
- Full audit trail of all promotions and rollbacks

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
Phase 3  ████████    EGM Data Layer              (Domain data — feeds Phase 4)
Phase 4  ██████      EGM Forecaster              (ML — feeds Phase 5)
Phase 5  ██████      Contract Engine             (Business logic — feeds Phase 6)
Phase 6  ██████      RE Capital Filter           (Pipeline — uses everything)
Phase 7  ████        Portfolio Brain             (Aggregation layer)
Phase 8  ████        ArkainBrain Integration     (LLM orchestration over working platform)
Phase 9  ████        Continuous Learning          (Operational maturity)
```

### Parallelism Opportunities
- Phase 2 (financial tools) can start as soon as Phase 0B (DB) is done — doesn't need full ACP
- Phase 3 (EGM data) can partially overlap with Phase 2 (different team/focus)
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
