"""
engine.api_server — FastAPI Application
==========================================
Phase 0D: Production API server with DB-backed persistence.

Replaces the Flask stub in api.py with a real FastAPI application.
Wires JWT auth, database sessions, and health checks.

Run:
    python -m engine.api_server                      # Direct
    uvicorn engine.api_server:app --reload --port 8000  # With hot reload
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from engine.db.settings import get_settings
from engine.db.session import (
    get_engine, get_session, get_session_factory,
    check_db_connection, create_all_tables,
)
from engine.db.repositories import (
    OrganizationRepo, WorkspaceRepo, UserRepo,
    APIKeyRepo, AuditLogRepo, JobRepo,
    AgentConfigRepo, ToolRunRepo,
)
from engine.db.acp_repositories import (
    ModelRouteRepo, ToolPolicyRepo, PipelineDefRepo, StrategyWeightsRepo,
)
from engine.tenants import (
    Organization, Workspace, UserIdentity, Job, JobStatus,
    Permission, AuthorizationError, ROLE_PERMISSIONS,
)
from engine.auth import (
    AuthMiddleware, JWTValidator, APIKeyAuth, AuthResult,
)

logger = logging.getLogger("engine.api")


# ═══════════════════════════════════════════════════════════════
# LIFESPAN — startup / shutdown
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown logic."""
    settings = get_settings()
    logger.info(f"Starting AgenticEngine API [env={settings.env}]")

    # Initialize DB engine
    engine = get_engine()
    logger.info(f"Database connected: {settings.db.host}:{settings.db.port}/{settings.db.name}")

    # In development, auto-create tables if they don't exist
    if settings.is_development:
        create_all_tables(engine)
        logger.info("Development mode: tables auto-created")

    yield

    # Shutdown
    logger.info("Shutting down AgenticEngine API")


# ═══════════════════════════════════════════════════════════════
# APP FACTORY
# ═══════════════════════════════════════════════════════════════

settings = get_settings()

app = FastAPI(
    title="AgenticEngine V2",
    description="Multi-tenant agentic AI pipeline platform",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — reads CORS_ORIGINS env var; defaults to ["*"] in dev, [] in prod
import os as _os
_cors_env = _os.getenv("CORS_ORIGINS", "")
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
elif settings.is_development:
    _cors_origins = ["*"]
else:
    _cors_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════
# DEPENDENCIES
# ═══════════════════════════════════════════════════════════════

def get_db_session():
    """Yield a DB session per request."""
    with get_session() as session:
        yield session


def get_auth_middleware() -> AuthMiddleware:
    """Build auth middleware from settings."""
    s = get_settings()
    jwt_validator = JWTValidator(
        secret=s.auth.jwt_secret,
        audience=s.auth.jwt_audience,
        issuer=s.auth.jwt_issuer,
    )
    return AuthMiddleware(
        jwt_validator=jwt_validator,
        allow_header_auth=s.auth.allow_header_auth,
    )


def authenticate(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    x_workspace_id: Optional[str] = Header(None),
) -> AuthResult:
    """Authenticate the request. Returns AuthResult or raises 401."""
    middleware = get_auth_middleware()
    headers = dict(request.headers)
    result = middleware.authenticate(headers)
    if not result.authenticated:
        raise HTTPException(status_code=401, detail=result.error)
    return result


# ═══════════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str
    version: str
    db_connected: bool
    timestamp: str


class JobSubmitRequest(BaseModel):
    pipeline_type: str
    config: Dict[str, Any] = {}


class JobResponse(BaseModel):
    job_id: str
    workspace_id: str
    user_id: str
    pipeline_type: str
    status: str
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""


class AgentConfigRequest(BaseModel):
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    timeout_sec: Optional[int] = None
    retry_count: Optional[int] = None
    tool_allowlist: Optional[List[str]] = None
    prompt_template: Optional[str] = None
    output_schema: Optional[Dict] = None
    enabled: Optional[bool] = None
    change_reason: str = ""


# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════

@app.get("/health", response_model=HealthResponse)
def health():
    """Health check — DB connectivity status."""
    db_ok = check_db_connection()
    status = "healthy" if db_ok else "degraded"
    return HealthResponse(
        status=status,
        version="2.0.0",
        db_connected=db_ok,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ═══════════════════════════════════════════════════════════════
# JOBS
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/jobs", status_code=201)
def submit_job(
    body: JobSubmitRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    job = Job(
        job_id=uuid.uuid4().hex[:12],
        workspace_id=auth.workspace_id,
        user_id=auth.user_id,
        pipeline_type=body.pipeline_type,
        config=body.config,
    )
    repo = JobRepo(session)
    repo.create(job)

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="job.submit",
        resource=f"job:{job.job_id}",
        outcome="success",
        user_id=auth.user_id,
        details={"pipeline_type": body.pipeline_type},
    )

    return {"job_id": job.job_id, "status": job.status}


@app.get("/api/v1/jobs/{job_id}")
def get_job(
    job_id: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = JobRepo(session)
    job = repo.get(job_id)
    if not job or job.workspace_id != auth.workspace_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.get("/api/v1/jobs")
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = JobRepo(session)
    jobs = repo.list_by_workspace(
        auth.workspace_id, limit=limit, status_filter=status
    )
    return {"jobs": [j.to_dict() for j in jobs], "count": len(jobs)}


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = JobRepo(session)
    job = repo.get(job_id)
    if not job or job.workspace_id != auth.workspace_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")

    repo.update_status(job_id, JobStatus.CANCELLED.value)

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="job.cancel",
        resource=f"job:{job_id}",
        outcome="success",
        user_id=auth.user_id,
    )

    return {"job_id": job_id, "status": "cancelled"}


# ═══════════════════════════════════════════════════════════════
# AGENT CONFIGS (Phase 1 — endpoints ready)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/agents")
def list_agent_configs(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = AgentConfigRepo(session)
    configs = repo.list_by_workspace(auth.workspace_id)
    return {"configs": configs, "count": len(configs)}


@app.get("/api/v1/agents/{agent_name}")
def get_agent_config(
    agent_name: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = AgentConfigRepo(session)
    config = repo.get(auth.workspace_id, agent_name)
    if not config:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return config


@app.put("/api/v1/agents/{agent_name}")
def upsert_agent_config(
    agent_name: str,
    body: AgentConfigRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    config_dict = {k: v for k, v in body.model_dump().items()
                   if v is not None and k != "change_reason"}

    repo = AgentConfigRepo(session)
    result = repo.upsert(
        workspace_id=auth.workspace_id,
        agent_name=agent_name,
        config=config_dict,
        changed_by=auth.user_id,
        change_reason=body.change_reason,
    )

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="agent_config.update",
        resource=f"agent:{agent_name}",
        outcome="success",
        user_id=auth.user_id,
        details={"version": result["version"], "changes": config_dict},
    )

    return result


@app.get("/api/v1/agents/{agent_name}/history")
def agent_config_history(
    agent_name: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = AgentConfigRepo(session)
    history = repo.get_version_history(auth.workspace_id, agent_name)
    return {"versions": history, "count": len(history)}


@app.post("/api/v1/agents/{agent_name}/rollback")
def rollback_agent_config(
    agent_name: str,
    target_version: int = Query(..., ge=1),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = AgentConfigRepo(session)
    result = repo.rollback(
        auth.workspace_id, agent_name, target_version,
        changed_by=auth.user_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Version not found")

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="agent_config.rollback",
        resource=f"agent:{agent_name}",
        outcome="success",
        user_id=auth.user_id,
        details={"target_version": target_version, "new_version": result["version"]},
    )

    return result


# ═══════════════════════════════════════════════════════════════
# MODEL ROUTES
# ═══════════════════════════════════════════════════════════════

class ModelRouteRequest(BaseModel):
    primary_provider: str
    primary_model: str
    fallback_provider: str = ""
    fallback_model: str = ""
    cost_cap_per_run: float = 10.0
    latency_cap_ms: int = 30000
    enabled: bool = True


@app.get("/api/v1/model-routes")
def list_model_routes(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ModelRouteRepo(session)
    routes = repo.list_by_workspace(auth.workspace_id)
    return {"routes": routes, "count": len(routes)}


@app.get("/api/v1/model-routes/{tier}")
def get_model_route(
    tier: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ModelRouteRepo(session)
    route = repo.get(auth.workspace_id, tier)
    if not route:
        raise HTTPException(status_code=404, detail="Model route not found")
    return route


@app.put("/api/v1/model-routes/{tier}")
def upsert_model_route(
    tier: str,
    body: ModelRouteRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ModelRouteRepo(session)
    result = repo.upsert(auth.workspace_id, tier, body.model_dump())

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="model_route.update",
        resource=f"route:{tier}",
        outcome="success",
        user_id=auth.user_id,
        details={"tier": tier, "model": body.primary_model},
    )
    return result


@app.delete("/api/v1/model-routes/{tier}")
def delete_model_route(
    tier: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ModelRouteRepo(session)
    if not repo.delete(auth.workspace_id, tier):
        raise HTTPException(status_code=404, detail="Model route not found")
    return {"deleted": True, "tier": tier}


# ═══════════════════════════════════════════════════════════════
# TOOL POLICIES
# ═══════════════════════════════════════════════════════════════

class ToolPolicyRequest(BaseModel):
    tool_name: str
    agent_name: str = "*"
    action_scope: str = "read"
    rate_limit_per_min: int = 60
    rate_limit_per_run: int = 100
    requires_approval: bool = False
    egress_allowed_domains: List[str] = []
    enabled: bool = True


class ToolPolicyUpdateRequest(BaseModel):
    action_scope: Optional[str] = None
    rate_limit_per_min: Optional[int] = None
    rate_limit_per_run: Optional[int] = None
    requires_approval: Optional[bool] = None
    egress_allowed_domains: Optional[List[str]] = None
    enabled: Optional[bool] = None


@app.get("/api/v1/tool-policies")
def list_tool_policies(
    agent_name: Optional[str] = Query(None),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ToolPolicyRepo(session)
    policies = repo.list_by_workspace(auth.workspace_id, agent_name=agent_name)
    return {"policies": policies, "count": len(policies)}


@app.post("/api/v1/tool-policies", status_code=201)
def create_tool_policy(
    body: ToolPolicyRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ToolPolicyRepo(session)
    result = repo.create(auth.workspace_id, body.model_dump())

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="tool_policy.create",
        resource=f"policy:{body.tool_name}",
        outcome="success",
        user_id=auth.user_id,
        details={"tool": body.tool_name, "agent": body.agent_name},
    )
    return result


@app.put("/api/v1/tool-policies/{policy_id}")
def update_tool_policy(
    policy_id: int,
    body: ToolPolicyUpdateRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ToolPolicyRepo(session)
    config = {k: v for k, v in body.model_dump().items() if v is not None}
    result = repo.update(policy_id, config)
    if not result:
        raise HTTPException(status_code=404, detail="Tool policy not found")

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="tool_policy.update",
        resource=f"policy:{policy_id}",
        outcome="success",
        user_id=auth.user_id,
        details=config,
    )
    return result


@app.delete("/api/v1/tool-policies/{policy_id}")
def delete_tool_policy(
    policy_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ToolPolicyRepo(session)
    if not repo.delete(policy_id):
        raise HTTPException(status_code=404, detail="Tool policy not found")
    return {"deleted": True, "policy_id": policy_id}


# ═══════════════════════════════════════════════════════════════
# PIPELINE DEFINITIONS
# ═══════════════════════════════════════════════════════════════

class PipelineDefRequest(BaseModel):
    stages: List[Dict[str, Any]]


@app.get("/api/v1/pipelines")
def list_pipelines(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = PipelineDefRepo(session)
    pipelines = repo.list_by_workspace(auth.workspace_id)
    return {"pipelines": pipelines, "count": len(pipelines)}


@app.get("/api/v1/pipelines/{name}")
def get_pipeline(
    name: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = PipelineDefRepo(session)
    pipeline = repo.get(auth.workspace_id, name)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return pipeline


@app.put("/api/v1/pipelines/{name}")
def upsert_pipeline(
    name: str,
    body: PipelineDefRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = PipelineDefRepo(session)
    result = repo.upsert(
        auth.workspace_id, name, body.stages,
        changed_by=auth.user_id,
    )

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="pipeline_def.update",
        resource=f"pipeline:{name}",
        outcome="success",
        user_id=auth.user_id,
        details={"version": result["version"], "stage_count": len(body.stages)},
    )
    return result


@app.delete("/api/v1/pipelines/{name}")
def delete_pipeline(
    name: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = PipelineDefRepo(session)
    if not repo.delete(auth.workspace_id, name):
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"deleted": True, "name": name}


# ═══════════════════════════════════════════════════════════════
# STRATEGY WEIGHTS
# ═══════════════════════════════════════════════════════════════

class StrategyWeightsRequest(BaseModel):
    mode_a_capital_filter: float = 0.25
    mode_b_vertical_integration: float = 0.25
    mode_c_regional_empire: float = 0.25
    mode_d_opportunistic: float = 0.25


@app.get("/api/v1/strategy-weights")
def get_strategy_weights(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = StrategyWeightsRepo(session)
    current = repo.get_current(auth.workspace_id)
    if not current:
        return {
            "mode_a_capital_filter": 0.25,
            "mode_b_vertical_integration": 0.25,
            "mode_c_regional_empire": 0.25,
            "mode_d_opportunistic": 0.25,
            "version": 0,
            "message": "Using defaults (no weights configured)",
        }
    return current


@app.post("/api/v1/strategy-weights", status_code=201)
def set_strategy_weights(
    body: StrategyWeightsRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    # Validate weights sum to ~1.0
    total = (
        body.mode_a_capital_filter + body.mode_b_vertical_integration +
        body.mode_c_regional_empire + body.mode_d_opportunistic
    )
    if abs(total - 1.0) > 0.01:
        raise HTTPException(
            status_code=422,
            detail=f"Weights must sum to 1.0 (got {total:.4f})"
        )

    repo = StrategyWeightsRepo(session)
    result = repo.set_weights(
        auth.workspace_id, body.model_dump(),
        created_by=auth.user_id,
    )

    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="strategy_weights.update",
        resource="strategy_weights",
        outcome="success",
        user_id=auth.user_id,
        details={"version": result["version"], "weights": body.model_dump()},
    )
    return result


@app.get("/api/v1/strategy-weights/history")
def strategy_weights_history(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = StrategyWeightsRepo(session)
    versions = repo.list_versions(auth.workspace_id)
    return {"versions": versions, "count": len(versions)}


# ═══════════════════════════════════════════════════════════════
# FINANCIAL TOOLS (Phase 2)
# ═══════════════════════════════════════════════════════════════

class ToolExecuteRequest(BaseModel):
    tool_name: str
    inputs: Dict[str, Any]


class ToolBatchRequest(BaseModel):
    tool_name: str
    inputs_list: List[Dict[str, Any]]


class ToolExportRequest(BaseModel):
    tool_name: str
    inputs: Dict[str, Any]
    format: str = "csv"       # "csv" or "pdf"
    title: str = ""


@app.get("/api/v1/tools")
def list_financial_tools():
    """List all available financial tools with input/output schemas."""
    from engine.financial.runner import ToolRunnerService
    runner = ToolRunnerService()
    return {"tools": runner.list_tools(), "count": len(runner.list_tools())}


@app.post("/api/v1/tools/execute")
def execute_financial_tool(
    body: ToolExecuteRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Execute a financial tool and persist the result."""
    from engine.financial.runner import ToolRunnerService, ToolExecutionError

    runner = ToolRunnerService(
        session=session,
        workspace_id=auth.workspace_id,
        user_id=auth.user_id,
    )

    try:
        result = runner.run(body.tool_name, body.inputs)
    except ToolExecutionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result


@app.post("/api/v1/tools/batch")
def batch_execute_financial_tool(
    body: ToolBatchRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Execute a financial tool multiple times (sensitivity sweeps)."""
    from engine.financial.runner import ToolRunnerService

    if len(body.inputs_list) > 100:
        raise HTTPException(status_code=422, detail="Max 100 items per batch")

    runner = ToolRunnerService(
        session=session,
        workspace_id=auth.workspace_id,
        user_id=auth.user_id,
    )

    results = runner.run_batch(body.tool_name, body.inputs_list)
    errors = sum(1 for r in results if "_meta" in r and "error" in r.get("_meta", {}))
    return {"results": results, "count": len(results), "errors": errors}


@app.post("/api/v1/tools/export")
def export_financial_tool(
    body: ToolExportRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Execute a tool and return the result as CSV or PDF."""
    from fastapi.responses import Response
    from engine.financial.runner import ToolRunnerService, ToolExecutionError
    from engine.financial.export import export_csv, export_pdf

    runner = ToolRunnerService(
        session=session,
        workspace_id=auth.workspace_id,
        user_id=auth.user_id,
    )

    try:
        result = runner.run(body.tool_name, body.inputs)
    except ToolExecutionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    meta = result.get("_meta", {})
    # Strip _meta for export
    clean_output = {k: v for k, v in result.items() if not k.startswith("_")}

    if body.format == "pdf":
        pdf_bytes = export_pdf(body.tool_name, body.inputs, clean_output, meta, body.title)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{body.tool_name}.pdf"'},
        )
    else:
        csv_bytes = export_csv(body.tool_name, body.inputs, clean_output, meta)
        return Response(
            content=csv_bytes,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{body.tool_name}.csv"'},
        )


@app.post("/api/v1/tools/register-policies")
def register_tool_policies(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Register default policies for all financial tools in this workspace."""
    from engine.financial.policies import register_financial_policies_db

    count = register_financial_policies_db(session, auth.workspace_id, auth.user_id)
    return {"registered": count, "message": f"Created {count} tool policies"}


# ═══════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/audit")
def get_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = AuditLogRepo(session)
    entries = repo.list_entries(
        auth.workspace_id, limit=limit, offset=offset,
        action_filter=action,
    )
    total = repo.count(auth.workspace_id)
    return {"entries": entries, "count": len(entries), "total": total}


# ═══════════════════════════════════════════════════════════════
# TOOL RUNS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/tools/runs")
def list_tool_runs(
    tool_name: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    repo = ToolRunRepo(session)
    runs = repo.list_runs(auth.workspace_id, tool_name=tool_name, limit=limit)
    return {"runs": runs, "count": len(runs)}


# ═══════════════════════════════════════════════════════════════
# EGM DATA — INGEST, LOCATIONS, PERFORMANCE, HEALTH (Phase 3)
# ═══════════════════════════════════════════════════════════════

class IngestRequest(BaseModel):
    source_name: str
    content: str
    report_month: str  # "YYYY-MM"
    run_type: str = "manual"


@app.post("/api/v1/egm/ingest", status_code=201)
def ingest_egm_data(
    body: IngestRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Ingest EGM data from raw CSV content."""
    from engine.egm.pipeline import IngestPipeline

    month = datetime.strptime(body.report_month, "%Y-%m").replace(
        day=1, tzinfo=timezone.utc
    )
    pipeline = IngestPipeline(session, auth.workspace_id)
    result = pipeline.ingest(
        source_name=body.source_name,
        content=body.content,
        report_month=month,
        triggered_by=auth.user_id,
        run_type=body.run_type,
    )
    return result


@app.get("/api/v1/egm/ingest/runs")
def list_ingest_runs(
    source_id: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.egm_repositories import IngestRunRepo
    repo = IngestRunRepo(session)
    runs = repo.list_runs(source_id, limit=limit)
    return {"runs": runs, "count": len(runs)}


@app.get("/api/v1/egm/ingest/runs/{run_id}/errors")
def list_ingest_errors(
    run_id: int,
    limit: int = Query(100, ge=1, le=500),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.egm_repositories import IngestErrorRepo
    repo = IngestErrorRepo(session)
    errors = repo.list_errors(run_id, limit=limit)
    return {"errors": errors, "count": len(errors)}


@app.get("/api/v1/egm/sources")
def list_data_sources(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.egm_repositories import DataSourceRepo
    repo = DataSourceRepo(session)
    sources = repo.list_all()
    return {"sources": sources, "count": len(sources)}


@app.get("/api/v1/egm/locations")
def search_locations(
    state: Optional[str] = Query(None),
    venue_type: Optional[str] = Query(None),
    municipality: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.egm_repositories import EGMLocationRepo
    repo = EGMLocationRepo(session)
    locations = repo.search(
        state=state, venue_type=venue_type, municipality=municipality,
        limit=limit, offset=offset,
    )
    total = repo.count(state=state)
    return {"locations": locations, "count": len(locations), "total": total}


@app.get("/api/v1/egm/locations/{location_id}")
def get_location(
    location_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.egm_repositories import EGMLocationRepo
    repo = EGMLocationRepo(session)
    location = repo.get(location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    return location


@app.get("/api/v1/egm/locations/{location_id}/history")
def get_location_history(
    location_id: int,
    months: int = Query(24, ge=1, le=120),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.analytics import EGMAnalytics
    analytics = EGMAnalytics(session)
    return analytics.location_trends(location_id, months=months)


@app.get("/api/v1/egm/locations/{location_id}/anomalies")
def get_location_anomalies(
    location_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.analytics import EGMAnalytics
    analytics = EGMAnalytics(session)
    anomalies = analytics.detect_anomalies(location_id)
    return {"anomalies": anomalies, "count": len(anomalies)}


@app.get("/api/v1/egm/performance")
def get_egm_performance(
    month: str = Query(..., description="YYYY-MM"),
    state: Optional[str] = Query(None),
    venue_type: Optional[str] = Query(None),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.analytics import EGMAnalytics
    report_month = datetime.strptime(month, "%Y-%m").replace(
        day=1, tzinfo=timezone.utc
    )
    analytics = EGMAnalytics(session)
    return analytics.performance_summary(report_month, state, venue_type)


@app.get("/api/v1/egm/top-performers")
def get_top_performers(
    month: str = Query(..., description="YYYY-MM"),
    state: Optional[str] = Query(None),
    metric: str = Query("net_win"),
    limit: int = Query(20, ge=1, le=100),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.analytics import EGMAnalytics
    report_month = datetime.strptime(month, "%Y-%m").replace(
        day=1, tzinfo=timezone.utc
    )
    analytics = EGMAnalytics(session)
    return analytics.top_performers(report_month, state, metric, limit)


@app.get("/api/v1/egm/health")
def egm_health(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.analytics import EGMAnalytics
    analytics = EGMAnalytics(session)
    return analytics.data_health_summary()


@app.get("/api/v1/egm/health/source/{source_id}")
def egm_source_health(
    source_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.analytics import EGMAnalytics
    analytics = EGMAnalytics(session)
    return analytics.source_health(source_id)


# ═══════════════════════════════════════════════════════════════
# EGM FORECASTER — PREDICT, TRAIN, MODEL REGISTRY (Phase 4)
# ═══════════════════════════════════════════════════════════════

class PredictRequest(BaseModel):
    venue_type: str
    state: str = "IL"
    terminal_count: int = 5
    municipality: str = ""
    location_id: Optional[int] = None
    target_month: Optional[str] = None  # "YYYY-MM"
    include_similar: bool = True


class TrainRequest(BaseModel):
    model_name: str = "egm_forecaster"


@app.post("/api/v1/egm/predict")
def egm_predict(
    body: PredictRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Predict coin_in, hold_pct, and net_win for an EGM location."""
    from engine.egm.prediction import PredictionService

    target_month = None
    if body.target_month:
        target_month = datetime.strptime(body.target_month, "%Y-%m").replace(
            day=1, tzinfo=timezone.utc
        )

    svc = PredictionService(session, auth.workspace_id, auth.user_id)
    return svc.predict(
        venue_type=body.venue_type,
        state=body.state,
        terminal_count=body.terminal_count,
        municipality=body.municipality,
        location_id=body.location_id,
        target_month=target_month,
        include_similar=body.include_similar,
    )


@app.post("/api/v1/egm/train")
def egm_train(
    body: TrainRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Train a new forecaster model version from current EGM data."""
    from engine.egm.prediction import PredictionService

    svc = PredictionService(session, auth.workspace_id, auth.user_id)
    return svc.train_model(model_name=body.model_name)


@app.get("/api/v1/egm/models")
def list_models(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.forecast_repositories import ModelRegistryRepo
    repo = ModelRegistryRepo(session)
    models = repo.list_models()
    return {"models": models, "count": len(models)}


@app.get("/api/v1/egm/models/{model_name}")
def get_model(
    model_name: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.egm.prediction import PredictionService
    svc = PredictionService(session, auth.workspace_id)
    return svc.get_model_info(model_name)


@app.post("/api/v1/egm/models/{model_name}/promote/{version}")
def promote_model(
    model_name: str,
    version: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.forecast_repositories import ModelRegistryRepo
    repo = ModelRegistryRepo(session)
    if not repo.promote(model_name, version, auth.user_id):
        raise HTTPException(status_code=404, detail="Model version not found")
    return {"promoted": True, "model_name": model_name, "version": version}


@app.get("/api/v1/egm/predictions")
def list_predictions(
    limit: int = Query(50, ge=1, le=200),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.forecast_repositories import PredictionLogRepo
    repo = PredictionLogRepo(session)
    preds = repo.list_recent(auth.workspace_id, limit=limit)
    return {"predictions": preds, "count": len(preds)}


# ═══════════════════════════════════════════════════════════════
# CONTRACT ENGINE — TEMPLATES, SIMULATION, ANALYSIS (Phase 5)
# ═══════════════════════════════════════════════════════════════

class CreateTemplateRequest(BaseModel):
    name: str
    agreement_type: str
    terms: Dict[str, Any]
    constraints: Optional[Dict[str, Any]] = None
    acquisition_type: str = "cash"
    state_applicability: str = ""
    approval_required: bool = False


class DealAnalysisRequest(BaseModel):
    agreement_type: str = "revenue_share"
    terms: Optional[Dict[str, Any]] = None
    prediction: Optional[Dict[str, Any]] = None
    template_id: Optional[int] = None
    overrides: Optional[Dict[str, Any]] = None
    num_simulations: int = 10000
    scenario_name: str = ""
    seed: Optional[int] = None


class CompareDealsRequest(BaseModel):
    prediction: Dict[str, Any]
    structures: List[Dict[str, Any]]
    acquisition_cost: float = 150000
    num_simulations: int = 5000
    seed: Optional[int] = None


@app.post("/api/v1/contracts/templates", status_code=201)
def create_contract_template(
    body: CreateTemplateRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.contract_repositories import ContractTemplateRepo
    from engine.contracts.templates import validate_terms

    errors = validate_terms(body.agreement_type, body.terms, body.constraints)
    if errors:
        raise HTTPException(status_code=422, detail=[
            {"field": e.field, "message": e.message} for e in errors
        ])

    repo = ContractTemplateRepo(session)
    return repo.create(
        workspace_id=auth.workspace_id,
        name=body.name,
        agreement_type=body.agreement_type,
        terms=body.terms,
        constraints=body.constraints,
        acquisition_type=body.acquisition_type,
        state_applicability=body.state_applicability,
        approval_required=body.approval_required,
        created_by=auth.user_id,
    )


@app.get("/api/v1/contracts/templates")
def list_contract_templates(
    agreement_type: Optional[str] = Query(None),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.contract_repositories import ContractTemplateRepo
    repo = ContractTemplateRepo(session)
    templates = repo.list_templates(auth.workspace_id, agreement_type)
    return {"templates": templates, "count": len(templates)}


@app.get("/api/v1/contracts/templates/{template_id}")
def get_contract_template(
    template_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.contract_repositories import ContractTemplateRepo
    repo = ContractTemplateRepo(session)
    tmpl = repo.get(template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tmpl


@app.post("/api/v1/contracts/analyze")
def analyze_deal(
    body: DealAnalysisRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Run Monte Carlo simulation for a deal."""
    from engine.contracts.analyzer import DealAnalyzer
    analyzer = DealAnalyzer(session, auth.workspace_id, auth.user_id)
    return analyzer.analyze_deal(
        agreement_type=body.agreement_type,
        terms=body.terms,
        prediction=body.prediction,
        template_id=body.template_id,
        overrides=body.overrides,
        num_simulations=body.num_simulations,
        scenario_name=body.scenario_name,
        seed=body.seed,
    )


@app.post("/api/v1/contracts/compare")
def compare_deals(
    body: CompareDealsRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Compare multiple contract structures on the same deal."""
    from engine.contracts.analyzer import DealAnalyzer
    analyzer = DealAnalyzer(session, auth.workspace_id, auth.user_id)
    return analyzer.compare_deals(
        prediction=body.prediction,
        structures=body.structures,
        acquisition_cost=body.acquisition_cost,
        num_simulations=body.num_simulations,
        seed=body.seed,
    )


@app.get("/api/v1/contracts/simulations")
def list_simulations(
    limit: int = Query(50, ge=1, le=200),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.contract_repositories import SimulationRunRepo
    repo = SimulationRunRepo(session)
    runs = repo.list_runs(auth.workspace_id, limit=limit)
    return {"simulations": runs, "count": len(runs)}


@app.get("/api/v1/contracts/simulations/{run_id}")
def get_simulation(
    run_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.contract_repositories import SimulationRunRepo
    repo = SimulationRunRepo(session)
    run = repo.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return run


@app.post("/api/v1/contracts/seed-templates")
def seed_default_templates(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Seed workspace with default contract templates."""
    from engine.db.contract_repositories import ContractTemplateRepo
    from engine.contracts.templates import default_templates

    repo = ContractTemplateRepo(session)
    created = 0
    for tmpl in default_templates():
        existing = repo.get_latest(auth.workspace_id, tmpl["name"])
        if not existing:
            repo.create(
                workspace_id=auth.workspace_id,
                name=tmpl["name"],
                agreement_type=tmpl["agreement_type"],
                terms=tmpl["terms"],
                constraints=tmpl.get("constraints", {}),
                state_applicability=tmpl.get("state_applicability", ""),
                created_by=auth.user_id,
            )
            created += 1
    return {"seeded": created, "total_defaults": len(default_templates())}


# ═══════════════════════════════════════════════════════════════
# REAL ESTATE DEAL PIPELINE (Phase 6)
# ═══════════════════════════════════════════════════════════════

class DealEvalRequest(BaseModel):
    deal_name: str
    property_type: str = "retail_strip"
    purchase_price: float
    address: str = ""
    state: str = ""
    noi: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    gaming_eligible: Optional[bool] = None
    template_id: Optional[int] = None
    market_context: Optional[Dict] = None
    overrides: Optional[Dict] = None


class DealWithGamingRequest(DealEvalRequest):
    gaming_prediction: Optional[Dict] = None


@app.post("/api/v1/deals/evaluate", status_code=201)
def evaluate_deal(
    body: DealEvalRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Run full 7-stage deal evaluation pipeline."""
    from engine.realestate.pipeline import DealPipeline

    inputs = body.model_dump(exclude_none=True, exclude={"market_context", "template_id"})
    if body.overrides:
        inputs.update(body.overrides)

    pipeline = DealPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.evaluate(
        inputs=inputs,
        market_context=body.market_context,
        template_id=body.template_id,
    )


@app.post("/api/v1/deals/evaluate-with-gaming", status_code=201)
def evaluate_deal_with_gaming(
    body: DealWithGamingRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Evaluate deal with integrated EGM prediction from Phase 4."""
    from engine.realestate.pipeline import DealPipeline

    inputs = body.model_dump(exclude_none=True, exclude={"market_context", "template_id", "gaming_prediction"})
    pipeline = DealPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.evaluate_with_gaming(
        inputs=inputs,
        gaming_prediction=body.gaming_prediction,
        market_context=body.market_context,
    )


@app.get("/api/v1/deals")
def list_deals(
    status: str = Query("", description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.deal_repositories import DealRunRepo
    repo = DealRunRepo(session)
    runs = repo.list_runs(auth.workspace_id, status=status, limit=limit)
    return {"deals": runs, "count": len(runs)}


@app.get("/api/v1/deals/{deal_id}")
def get_deal(
    deal_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.deal_repositories import DealRunRepo
    repo = DealRunRepo(session)
    deal = repo.get(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@app.post("/api/v1/deals/property-templates/seed")
def seed_property_templates(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Seed workspace with default property templates."""
    from engine.db.deal_repositories import PropertyTemplateRepo
    from engine.realestate.templates import default_property_templates

    repo = PropertyTemplateRepo(session)
    created = 0
    for tmpl in default_property_templates():
        existing = repo.get_by_type(auth.workspace_id, tmpl["property_type"])
        if not existing:
            repo.create(
                workspace_id=auth.workspace_id,
                name=tmpl["name"],
                property_type=tmpl["property_type"],
                defaults=tmpl["defaults"],
                scoring_weights=tmpl["scoring_weights"],
            )
            created += 1
    return {"seeded": created}


@app.get("/api/v1/deals/property-templates/list")
def list_property_templates(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.deal_repositories import PropertyTemplateRepo
    repo = PropertyTemplateRepo(session)
    templates = repo.list_templates(auth.workspace_id)
    return {"templates": templates, "count": len(templates)}


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO BRAIN (Phase 7)
# ═══════════════════════════════════════════════════════════════

class PortfolioAssetRequest(BaseModel):
    name: str
    property_type: str = ""
    asset_type: str = ""
    address: str = ""
    state: str = ""
    municipality: str = ""
    acquisition_date: str = ""
    acquisition_cost: float = 0
    current_value: float = 0
    ownership_type: str = "owned"
    contract_type: str = ""
    has_gaming: bool = False
    terminal_count: int = 0


class PortfolioDebtRequest(BaseModel):
    asset_id: int
    lender: str = ""
    original_balance: float = 0
    current_balance: float = 0
    annual_rate: float = 0
    monthly_payment: float = 0
    maturity_date: str = ""


class DealImpactRequest(BaseModel):
    name: str = ""
    state: str = ""
    property_type: str = ""
    current_value: float = 0
    debt_amount: float = 0
    has_gaming: bool = False
    ownership_type: str = "owned"


@app.post("/api/v1/portfolio/assets", status_code=201)
def create_portfolio_asset(
    body: PortfolioAssetRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.portfolio_repositories import PortfolioAssetRepo
    repo = PortfolioAssetRepo(session)
    return repo.create(
        workspace_id=auth.workspace_id, name=body.name,
        property_type=body.property_type, asset_type=body.asset_type,
        address=body.address, state=body.state,
        municipality=body.municipality,
        acquisition_date=body.acquisition_date,
        acquisition_cost=body.acquisition_cost,
        current_value=body.current_value,
        ownership_type=body.ownership_type,
        contract_type=body.contract_type,
        has_gaming=body.has_gaming,
        terminal_count=body.terminal_count,
    )


@app.get("/api/v1/portfolio/assets")
def list_portfolio_assets(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.portfolio_repositories import PortfolioAssetRepo
    repo = PortfolioAssetRepo(session)
    assets = repo.list_assets(auth.workspace_id)
    return {"assets": assets, "count": len(assets)}


@app.get("/api/v1/portfolio/assets/{asset_id}")
def get_portfolio_asset(
    asset_id: int,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.portfolio_repositories import PortfolioAssetRepo
    repo = PortfolioAssetRepo(session)
    asset = repo.get(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@app.post("/api/v1/portfolio/debt", status_code=201)
def create_portfolio_debt(
    body: PortfolioDebtRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.db.portfolio_repositories import PortfolioDebtRepo
    repo = PortfolioDebtRepo(session)
    return repo.create(
        asset_id=body.asset_id, workspace_id=auth.workspace_id,
        lender=body.lender, original_balance=body.original_balance,
        current_balance=body.current_balance,
        annual_rate=body.annual_rate,
        monthly_payment=body.monthly_payment,
        maturity_date=body.maturity_date,
    )


@app.get("/api/v1/portfolio/dashboard")
def portfolio_dashboard(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.portfolio.analytics import PortfolioAnalytics
    analytics = PortfolioAnalytics(session, auth.workspace_id)
    return analytics.dashboard()


@app.post("/api/v1/portfolio/deal-impact")
def portfolio_deal_impact(
    body: DealImpactRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.portfolio.analytics import PortfolioAnalytics
    analytics = PortfolioAnalytics(session, auth.workspace_id)
    return analytics.new_deal_impact(body.model_dump())


# ═══════════════════════════════════════════════════════════════
# ARKAINBRAIN AGENT ORCHESTRATION (Phase 8)
# ═══════════════════════════════════════════════════════════════

class AgentRunRequest(BaseModel):
    agent_name: str
    task: str
    context: Dict[str, Any] = {}
    max_tool_calls: int = 10
    require_approval: bool = False


class PipelineRunRequest(BaseModel):
    pipeline_name: str
    inputs: Dict[str, Any] = {}


@app.post("/api/v1/brain/run")
def brain_run_agent(
    body: AgentRunRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Execute an agent on a task."""
    from engine.brain.adapter import ArkainBrainAdapter
    from dataclasses import asdict
    adapter = ArkainBrainAdapter(session, auth.workspace_id, auth.user_id)
    adapter.register_tools()
    result = adapter.run_agent(
        agent_name=body.agent_name, task=body.task,
        context=body.context,
        max_tool_calls=body.max_tool_calls,
        require_approval=body.require_approval,
    )
    return asdict(result)


@app.post("/api/v1/brain/pipeline")
def brain_run_pipeline(
    body: PipelineRunRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Execute a multi-stage pipeline."""
    from engine.brain.adapter import ArkainBrainAdapter, PipelineOrchestrator
    adapter = ArkainBrainAdapter(session, auth.workspace_id, auth.user_id)
    adapter.register_tools()
    orch = PipelineOrchestrator(adapter)
    return orch.run_pipeline(body.pipeline_name, body.inputs)


@app.get("/api/v1/brain/tools")
def brain_list_tools(
    category: str = Query("", description="Filter by category"),
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    from engine.brain.tools import ToolRegistry
    registry = ToolRegistry(session, auth.workspace_id)
    registry.register_all()
    tools = registry.list_tools(category)
    return {"tools": tools, "count": len(tools)}


# ═══════════════════════════════════════════════════════════════
# CONTINUOUS LEARNING (Phase 9)
# ═══════════════════════════════════════════════════════════════

class RetrainRequest(BaseModel):
    model_name: str = "egm_forecaster"
    auto_promote: bool = False


@app.post("/api/v1/learning/retrain")
def retrain_model(
    body: RetrainRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Trigger model retraining (champion/challenger)."""
    from engine.brain.learning import RetrainingPipeline
    from dataclasses import asdict
    pipeline = RetrainingPipeline(session, auth.workspace_id, auth.user_id)
    result = pipeline.retrain(
        model_name=body.model_name,
        auto_promote=body.auto_promote,
    )
    return asdict(result)


@app.post("/api/v1/learning/drift-check")
def check_drift(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Check for model drift against recent actuals."""
    from engine.brain.learning import DriftDetector
    detector = DriftDetector()
    # In production, pull predictions and actuals from DB
    # For now, return the detector config
    return {
        "warning_threshold": detector.warning_threshold,
        "critical_threshold": detector.critical_threshold,
        "market_shift_threshold": detector.market_shift_threshold,
        "message": "Drift check requires prediction log + recent actuals data",
    }


# ═══════════════════════════════════════════════════════════════
# STRATEGIC INTELLIGENCE LAYER
# ═══════════════════════════════════════════════════════════════

class StrategicAnalyzeRequest(BaseModel):
    title: str
    scenario_text: str
    objectives: List[str] = []
    constraints: List[str] = []
    time_horizon: str = "medium"
    budget_usd: float = 0.0
    risk_tolerance: str = "moderate"
    assumptions: List[str] = []
    template_type: Optional[str] = None
    tags: List[str] = []


class StrategicSWOTRequest(BaseModel):
    title: str = "SWOT Analysis"
    scenario_text: str
    objectives: List[str] = []
    constraints: List[str] = []


class StrategicScenariosRequest(BaseModel):
    title: str = "Scenario Simulation"
    scenario_text: str
    objectives: List[str] = []
    risk_tolerance: str = "moderate"
    budget_usd: float = 0.0


class StrategicMemoRequest(BaseModel):
    run_id: str
    format: str = "markdown"  # markdown, json, csv


class MarketResearchRequest(BaseModel):
    address: str
    property_type: str = "gas_station"
    context: str = ""
    city: str = ""
    state: str = ""
    county: str = ""
    purchase_price: float = 0
    noi: float = 0
    terminal_count: int = 0
    format: str = "json"  # json, markdown


@app.post("/api/v1/strategic/analyze")
def strategic_analyze(
    body: StrategicAnalyzeRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Run full strategic analysis pipeline (5 stages, multi-LLM routed)."""
    from engine.strategic.pipeline import StrategicPipeline
    from engine.strategic.templates import get_template

    # Apply template defaults if specified
    inputs = body.model_dump()
    if body.template_type:
        tmpl = get_template(body.template_type)
        if not inputs.get("objectives"):
            inputs["objectives"] = tmpl.get("default_objectives", [])
        if not inputs.get("constraints"):
            inputs["constraints"] = tmpl.get("default_constraints", [])
        stage_routes = tmpl.get("stage_routes")
    else:
        stage_routes = None

    # Check for DB-stored stage routes (Phase 7)
    db_routes = _load_stage_routes(session, auth.workspace_id, body.template_type or "general")
    if db_routes:
        stage_routes = db_routes

    pipeline = StrategicPipeline(
        session, auth.workspace_id, auth.user_id,
        stage_routes=stage_routes,
    )
    result = pipeline.analyze(inputs)

    # Audit
    AuditLogRepo(session).append(
        workspace_id=auth.workspace_id,
        action="strategic.analyze",
        resource=f"scenario:{result.get('scenario_id', '')}",
        outcome=result.get("decision", ""),
        user_id=auth.user_id,
        details={
            "run_id": result.get("run_id"),
            "confidence": result.get("confidence"),
            "elapsed_ms": result.get("elapsed_ms"),
        },
    )

    return result


@app.post("/api/v1/strategic/swot")
def strategic_swot(
    body: StrategicSWOTRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Generate SWOT analysis only."""
    from engine.strategic.pipeline import StrategicPipeline
    pipeline = StrategicPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.swot_only(body.model_dump())


@app.post("/api/v1/strategic/scenarios")
def strategic_scenarios(
    body: StrategicScenariosRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Generate scenario cases (bull/base/bear) with sensitivities."""
    from engine.strategic.pipeline import StrategicPipeline
    pipeline = StrategicPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.scenario_simulate(body.model_dump())


@app.post("/api/v1/strategic/stress-test")
def strategic_stress_test(
    body: StrategicSWOTRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Run stress test: failure modes, contradictions, second-order effects."""
    from engine.strategic.pipeline import StrategicPipeline
    pipeline = StrategicPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.stress_test(body.model_dump())


@app.post("/api/v1/strategic/memo")
def strategic_memo_export(
    body: StrategicMemoRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Export a strategic run as memo artifact."""
    from engine.db.strategic_repositories import StrategicRunRepo, StrategicArtifactRepo
    from engine.strategic.export import (
        export_memo_markdown, export_summary_json, export_actions_csv,
    )

    repo = StrategicRunRepo(session)
    run = repo.get_by_run_id(body.run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    outputs = run.get("outputs", {})
    if body.format == "markdown":
        content = export_memo_markdown(outputs)
        artifact_type = "md"
    elif body.format == "json":
        content = export_summary_json(outputs)
        artifact_type = "json"
    elif body.format == "csv":
        content = export_actions_csv(outputs)
        artifact_type = "csv"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown format: {body.format}")

    # Save artifact reference
    art_repo = StrategicArtifactRepo(session)
    art = art_repo.create(body.run_id, artifact_type, f"memo_{body.run_id}.{artifact_type}")

    return {
        "run_id": body.run_id,
        "format": body.format,
        "artifact_id": art.get("id"),
        "content": content,
    }


@app.post("/api/v1/strategic/research")
def strategic_market_research(
    body: MarketResearchRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Run deep market research for a site/address.

    Executes 15-20 targeted web searches across demographics, traffic,
    competition, gaming market, real estate comps, regulatory, economic,
    and risk domains. Synthesizes findings into structured report.
    """
    from engine.strategic.research import MarketResearcher, format_research_markdown

    researcher = MarketResearcher(session, auth.workspace_id, auth.user_id)
    report = researcher.research_site(
        address=body.address,
        property_type=body.property_type,
        context=body.context,
        city=body.city,
        state=body.state,
        county=body.county,
        purchase_price=body.purchase_price,
        noi=body.noi,
        terminal_count=body.terminal_count,
    )

    if body.format == "markdown":
        return {
            "report": report,
            "markdown": format_research_markdown(report),
        }

    return {"report": report}


# ═══════════════════════════════════════════════════════════════
# CONSTRUCTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════

class ConstructionEstimateRequest(BaseModel):
    property_type: str = "gas_station"
    project_type: str = "renovation"
    sqft: float = 2000
    terminal_count: int = 0
    state: str = "IL"
    city: str = ""


class ConstructionAnalysisRequest(BaseModel):
    scope: Optional[Dict[str, Any]] = None
    document_text: str = ""
    address: str = ""
    state: str = "IL"
    city: str = ""
    budget: float = 0
    acquisition_price: float = 0
    noi: float = 0


@app.post("/api/v1/construction/estimate")
def construction_quick_estimate(
    body: ConstructionEstimateRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Quick construction cost estimate from basic parameters.

    Returns cost estimate, schedule, manpower takeoff, and feasibility.
    """
    from engine.construction.pipeline import ConstructionPipeline
    pipeline = ConstructionPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.quick_estimate(
        property_type=body.property_type,
        project_type=body.project_type,
        sqft=body.sqft,
        terminal_count=body.terminal_count,
        state=body.state,
        city=body.city,
    )


@app.post("/api/v1/construction/analyze")
def construction_full_analysis(
    body: ConstructionAnalysisRequest,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Full construction feasibility analysis.

    Provide scope dict and/or document text for LLM extraction.
    Returns detailed cost estimate, schedule, manpower, and go/no-go.
    """
    from engine.construction.pipeline import ConstructionPipeline
    pipeline = ConstructionPipeline(session, auth.workspace_id, auth.user_id)
    return pipeline.analyze(
        scope=body.scope,
        document_text=body.document_text,
        address=body.address,
        state=body.state,
        city=body.city,
        budget=body.budget,
        acquisition_price=body.acquisition_price,
        noi=body.noi,
    )


@app.get("/api/v1/construction/cost-factors/{state}")
def construction_cost_factors(
    state: str,
    city: str = "",
    auth: AuthResult = Depends(authenticate),
):
    """Get location cost adjustment factors for a state/city."""
    from engine.construction.costs import get_location_factor, LABOR_RATES, LOCATION_FACTORS
    factor = get_location_factor(state.upper(), city)
    return {
        "state": state.upper(),
        "city": city,
        "location_factor": factor,
        "labor_rates": LABOR_RATES,
        "interpretation": f"Costs in {state.upper()} are {factor:.0%} of national average",
    }


# ── Multi-Provider Search ────────────────────────────────────

@app.post("/api/v1/search/multi")
def search_multi(
    request: Request,
    auth: AuthResult = Depends(authenticate),
):
    """Multi-provider search across Serper, Anthropic, and Google."""
    from engine.strategic.search_providers import multi_search
    body = request.state.body if hasattr(request.state, "body") else {}
    resp = multi_search(
        query=body.get("query", ""),
        num_results=body.get("num_results", 8),
        search_type=body.get("search_type", "search"),
        location=body.get("location", ""),
    )
    return resp.to_dict()


@app.post("/api/v1/search/news")
def search_news(
    request: Request,
    auth: AuthResult = Depends(authenticate),
):
    """Search recent news articles."""
    from engine.strategic.search_providers import multi_search
    body = request.state.body if hasattr(request.state, "body") else {}
    resp = multi_search(
        query=body.get("query", ""),
        num_results=body.get("num_results", 8),
        search_type="news",
    )
    return resp.to_dict()


@app.post("/api/v1/search/local")
def search_local(
    request: Request,
    auth: AuthResult = Depends(authenticate),
):
    """Search for local businesses and places."""
    from engine.strategic.search_providers import multi_search
    body = request.state.body if hasattr(request.state, "body") else {}
    resp = multi_search(
        query=body.get("query", ""),
        num_results=body.get("num_results", 8),
        search_type="places",
        location=body.get("location", ""),
    )
    return resp.to_dict()


# ── Vector Store / Market Memory ─────────────────────────────

@app.post("/api/v1/memory/similar-sites")
def memory_similar_sites(
    request: Request,
    auth: AuthResult = Depends(authenticate),
):
    """Find previously researched sites similar to an address."""
    from engine.strategic.vector_store import VectorStore
    body = request.state.body if hasattr(request.state, "body") else {}
    vs = VectorStore(auth.workspace_id)
    results = vs.find_similar_sites(
        body.get("address", ""),
        top_k=body.get("top_k", 5),
    )
    return {"similar_sites": results, "count": len(results)}


@app.post("/api/v1/memory/construction-comps")
def memory_construction_comps(
    request: Request,
    auth: AuthResult = Depends(authenticate),
):
    """Find historical construction cost comparables."""
    from engine.strategic.vector_store import VectorStore
    body = request.state.body if hasattr(request.state, "body") else {}
    vs = VectorStore(auth.workspace_id)
    results = vs.find_similar_construction(
        body.get("project_type", ""),
        body.get("location", ""),
        top_k=body.get("top_k", 5),
    )
    return {"comps": results, "count": len(results)}


@app.post("/api/v1/memory/trends")
def memory_market_trends(
    request: Request,
    auth: AuthResult = Depends(authenticate),
):
    """Get trend history for a market metric."""
    from engine.strategic.vector_store import VectorStore
    body = request.state.body if hasattr(request.state, "body") else {}
    vs = VectorStore(auth.workspace_id)
    results = vs.get_trend_history(
        metric=body.get("metric", ""),
        location=body.get("location", ""),
        state=body.get("state", ""),
        top_k=body.get("top_k", 20),
    )
    return {"trends": results, "metric": body.get("metric"), "count": len(results)}


@app.get("/api/v1/strategic/runs")
def strategic_list_runs(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """List recent strategic analysis runs."""
    from engine.db.strategic_repositories import StrategicRunRepo
    repo = StrategicRunRepo(session)
    return {"runs": repo.list_by_workspace(auth.workspace_id)}


@app.get("/api/v1/strategic/runs/{run_id}")
def strategic_get_run(
    run_id: str,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Get a specific strategic run by run_id."""
    from engine.db.strategic_repositories import StrategicRunRepo
    repo = StrategicRunRepo(session)
    run = repo.get_by_run_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/api/v1/strategic/scenarios")
def strategic_list_scenarios(
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """List saved scenarios for workspace."""
    from engine.db.strategic_repositories import StrategicScenarioRepo
    repo = StrategicScenarioRepo(session)
    return {"scenarios": repo.list_by_workspace(auth.workspace_id)}


@app.get("/api/v1/strategic/templates")
def strategic_list_templates():
    """List available scenario templates."""
    from engine.strategic.templates import default_scenario_templates
    return {"templates": default_scenario_templates()}


# ── Phase 7: Stage Routes CRUD ──────────────────────────────

@app.get("/api/v1/strategic/stage-routes")
def strategic_get_stage_routes(
    template_type: str = "general",
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Get stage-to-LLM-route mapping for a template type."""
    routes = _load_stage_routes(session, auth.workspace_id, template_type)
    if not routes:
        from engine.strategic.pipeline import DEFAULT_STAGE_ROUTES
        routes = dict(DEFAULT_STAGE_ROUTES)
    return {"template_type": template_type, "stage_routes": routes}


class StageRouteUpdate(BaseModel):
    stage_name: str
    route_tier: str


@app.put("/api/v1/strategic/stage-routes/{template_type}")
def strategic_update_stage_route(
    template_type: str,
    body: StageRouteUpdate,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Update a stage route for a template type (Phase 7)."""
    try:
        from engine.db.models import StrategicStageRouteRow
        existing = (
            session.query(StrategicStageRouteRow)
            .filter_by(
                workspace_id=auth.workspace_id,
                template_type=template_type,
                stage_name=body.stage_name,
            )
            .first()
        )
        if existing:
            existing.route_tier = body.route_tier
        else:
            session.add(StrategicStageRouteRow(
                workspace_id=auth.workspace_id,
                template_type=template_type,
                stage_name=body.stage_name,
                route_tier=body.route_tier,
            ))
        session.flush()

        AuditLogRepo(session).append(
            workspace_id=auth.workspace_id,
            action="strategic.stage_route.update",
            resource=f"route:{template_type}/{body.stage_name}",
            outcome="success",
            user_id=auth.user_id,
            details={"route_tier": body.route_tier},
        )

        return {"status": "updated", "stage": body.stage_name, "route": body.route_tier}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _load_stage_routes(session, workspace_id: str, template_type: str) -> Dict[str, str]:
    """Load DB-backed stage routes. Returns empty dict if none found."""
    try:
        from engine.db.models import StrategicStageRouteRow
        rows = (
            session.query(StrategicStageRouteRow)
            .filter_by(workspace_id=workspace_id, template_type=template_type, enabled=True)
            .all()
        )
        if not rows:
            return {}
        return {r.stage_name: r.route_tier for r in rows}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
# RATE LIMITING MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

import collections as _collections

# Sliding-window rate limiter (in-memory, per-IP)
_rate_buckets: Dict[str, list] = {}
_RATE_LIMIT_DEFAULT = int(_os.getenv("RATE_LIMIT_PER_MIN", "100"))
_RATE_LIMIT_HEAVY = int(_os.getenv("RATE_LIMIT_HEAVY_PER_MIN", "20"))
_RATE_LIMIT_INGEST = int(_os.getenv("RATE_LIMIT_INGEST_PER_MIN", "5"))

_HEAVY_PREFIXES = ("/api/v1/tools/execute", "/api/v1/brain/run",
                    "/api/v1/brain/pipeline", "/api/v1/learning/retrain",
                    "/api/v1/strategic/analyze")
_INGEST_PREFIXES = ("/api/v1/egm/ingest", "/api/v1/egm/train")


# ── Agent Role Management ────────────────────────────────────

@app.get("/api/v1/agents")
def list_agents(auth: AuthResult = Depends(authenticate)):
    """List all available agent roles."""
    from engine.brain.agents import list_agent_roles
    return {"agents": list_agent_roles()}


@app.get("/api/v1/agents/{agent_name}")
def get_agent(agent_name: str, auth: AuthResult = Depends(authenticate)):
    """Get a specific agent profile."""
    from engine.brain.agents import resolve_agent
    profile = resolve_agent(agent_name)
    return profile.to_dict()


@app.post("/api/v1/agents/{agent_name}/run")
def run_agent(
    agent_name: str,
    request: Request,
    auth: AuthResult = Depends(authenticate),
    session=Depends(get_db_session),
):
    """Execute an agent on a task."""
    from engine.brain.agents import resolve_agent
    from engine.brain.adapter import ArkainBrainAdapter

    body = request.state.body if hasattr(request.state, "body") else {}
    task = body.get("task", "")
    context = body.get("context", {})

    profile = resolve_agent(agent_name, session, auth.workspace_id)
    if not profile.is_active:
        raise HTTPException(status_code=400, detail=f"Agent '{agent_name}' is inactive")

    adapter = ArkainBrainAdapter(session, auth.workspace_id, auth.user_id)
    adapter.register_tools()

    result = adapter.run_agent(
        agent_name=agent_name,
        task=task,
        context=context,
        max_tool_calls=profile.max_tool_calls,
    )
    return result


@app.get("/api/v1/providers")
def list_providers(auth: AuthResult = Depends(authenticate)):
    """List configured LLM providers and their status."""
    import os
    from engine.strategic.llm_client import ROUTE_MODELS, TOKEN_COSTS
    providers = {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "perplexity": bool(os.environ.get("PERPLEXITY_API_KEY")),
        "serper": bool(os.environ.get("SERPER_API_KEY")),
        "qdrant": bool(os.environ.get("QDRANT_URL")),
        "voyage": bool(os.environ.get("VOYAGE_API_KEY")),
    }
    return {
        "providers": providers,
        "active_count": sum(1 for v in providers.values() if v),
        "routes": {k: {"provider": v["provider"], "model": v["model"]}
                   for k, v in ROUTE_MODELS.items()},
        "cost_table": TOKEN_COSTS,
    }


@app.get("/api/v1/strategic/extension-stages")
def list_extension_stages(auth: AuthResult = Depends(authenticate)):
    """List available extension pipeline stages."""
    return {
        "stages": [
            {"name": "data_gathering", "description": "Exhaustive data collection before analysis",
             "position": "before_compression", "tools": 8},
            {"name": "counterparty_risk", "description": "Assess counterparty financial health and reliability",
             "position": "after_compression", "tools": 3},
            {"name": "legal_risk", "description": "Legal and regulatory risk assessment",
             "position": "after_compression", "tools": 4},
            {"name": "capital_stack", "description": "Capital structure optimization",
             "position": "after_scenarios", "tools": 6},
        ],
    }


def _get_rate_limit(path: str) -> int:
    if any(path.startswith(p) for p in _INGEST_PREFIXES):
        return _RATE_LIMIT_INGEST
    if any(path.startswith(p) for p in _HEAVY_PREFIXES):
        return _RATE_LIMIT_HEAVY
    return _RATE_LIMIT_DEFAULT


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Skip health checks and dev mode
    if request.url.path == "/health" or settings.is_development:
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{request.url.path.split('/')[3] if request.url.path.count('/') >= 3 else 'root'}"
    now = time.time()
    limit = _get_rate_limit(request.url.path)

    # Initialize or clean bucket
    if key not in _rate_buckets:
        _rate_buckets[key] = []
    bucket = _rate_buckets[key]

    # Remove entries older than 60 seconds
    cutoff = now - 60
    _rate_buckets[key] = [t for t in bucket if t > cutoff]
    bucket = _rate_buckets[key]

    if len(bucket) >= limit:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded", "retry_after_seconds": 60},
            headers={"Retry-After": "60"},
        )

    bucket.append(now)
    return await call_next(request)


# ═══════════════════════════════════════════════════════════════
# REQUEST LOGGING MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed_ms = round((time.time() - start) * 1000)

    if request.url.path != "/health":
        logger.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} ({elapsed_ms}ms)"
        )

    response.headers["X-Request-Time-Ms"] = str(elapsed_ms)
    return response


# ═══════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    uvicorn.run(
        "engine.api_server:app",
        host=s.api_host,
        port=s.api_port,
        reload=s.is_development,
    )
