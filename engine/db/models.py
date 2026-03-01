"""
engine.db.models — SQLAlchemy ORM Models
==========================================
Phase 0A: Persistence layer for the existing in-memory engine.

Maps every in-memory dataclass to a Postgres table while preserving
the existing engine's interfaces. The engine modules continue to work
with their dataclasses; repositories handle the translation.

Tables created here:
  Platform Core:
    - organizations
    - workspaces
    - users
    - memberships (user ↔ workspace + role)
    - api_keys
    - audit_logs
    - jobs

  Agent Control Plane (Phase 1 — schema laid now, populated later):
    - agent_configs
    - agent_config_versions
    - model_routes
    - tool_policies
    - pipeline_defs
    - strategy_weights
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index, Integer,
    Numeric, String, Text, UniqueConstraint, ForeignKey,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ═══════════════════════════════════════════════════════════════
# BASE
# ═══════════════════════════════════════════════════════════════

class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


def utcnow():
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# PLATFORM CORE
# ═══════════════════════════════════════════════════════════════

class OrganizationRow(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    # Relationships
    workspaces = relationship("WorkspaceRow", back_populates="organization")


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    # Relationships
    organization = relationship("OrganizationRow", back_populates="workspaces")
    memberships = relationship("MembershipRow", back_populates="workspace")


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id"), nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    # Relationships
    memberships = relationship("MembershipRow", back_populates="user")


class MembershipRow(Base):
    """User ↔ Workspace membership with role."""
    __tablename__ = "memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False  # 'owner', 'admin', 'operator', 'viewer'
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_user_workspace"),
    )

    # Relationships
    user = relationship("UserRow", back_populates="memberships")
    workspace = relationship("WorkspaceRow", back_populates="memberships")


class APIKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # key_id
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id"), nullable=False
    )
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_api_keys_hash", "key_hash"),
    )


class AuditLogRow(Base):
    """Append-only audit log. No UPDATE or DELETE allowed at app layer."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(255), default="")
    outcome: Mapped[str] = mapped_column(String(32), default="")  # 'success', 'denied', 'error'
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    __table_args__ = (
        Index("ix_audit_ws_action", "workspace_id", "action"),
        Index("ix_audit_ws_time", "workspace_id", "created_at"),
    )


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True
    )
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_jobs_ws_status", "workspace_id", "status"),
    )


# ═══════════════════════════════════════════════════════════════
# AGENT CONTROL PLANE (Phase 1 — tables created now)
# ═══════════════════════════════════════════════════════════════

class AgentConfigRow(Base):
    """Active agent configuration. One row per agent per workspace."""
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    model_provider: Mapped[str] = mapped_column(String(64), default="openai")
    model_name: Mapped[str] = mapped_column(String(128), default="gpt-4o")
    max_tokens: Mapped[int] = mapped_column(Integer, default=128000)
    temperature: Mapped[float] = mapped_column(Float, default=0.5)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=300)
    retry_count: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff: Mapped[float] = mapped_column(Float, default=2.0)
    tool_allowlist: Mapped[list] = mapped_column(JSONB, default=list)
    prompt_template: Mapped[str] = mapped_column(Text, default="")
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    agent_weight: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64), default="")

    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_name", name="uq_ws_agent"),
        Index("ix_agent_configs_ws", "workspace_id"),
    )


class AgentConfigVersionRow(Base):
    """Append-only version history for agent configs."""
    __tablename__ = "agent_config_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_configs.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), default="")
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        Index("ix_acv_config_version", "agent_config_id", "version"),
    )


class ModelRouteRow(Base):
    __tablename__ = "model_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    tier: Mapped[str] = mapped_column(String(32), nullable=False)  # 'premium', 'heavy', 'light'
    primary_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_model: Mapped[str] = mapped_column(String(128), nullable=False)
    fallback_provider: Mapped[str] = mapped_column(String(64), default="")
    fallback_model: Mapped[str] = mapped_column(String(128), default="")
    cost_cap_per_run: Mapped[float] = mapped_column(Float, default=10.0)
    latency_cap_ms: Mapped[int] = mapped_column(Integer, default=30000)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "tier", name="uq_ws_tier"),
    )


class ToolPolicyRow(Base):
    __tablename__ = "tool_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(128), default="*")  # '*' = all agents
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    action_scope: Mapped[str] = mapped_column(String(32), default="read")
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=60)
    rate_limit_per_run: Mapped[int] = mapped_column(Integer, default=100)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    egress_allowed_domains: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_tool_policies_ws", "workspace_id"),
    )


class PipelineDefRow(Base):
    __tablename__ = "pipeline_defs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    stages: Mapped[list] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_ws_pipeline"),
    )


class StrategyWeightsRow(Base):
    __tablename__ = "strategy_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    mode_a_capital_filter: Mapped[float] = mapped_column(Float, default=0.25)
    mode_b_vertical_integration: Mapped[float] = mapped_column(Float, default=0.25)
    mode_c_regional_empire: Mapped[float] = mapped_column(Float, default=0.25)
    mode_d_opportunistic: Mapped[float] = mapped_column(Float, default=0.25)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64), default="")


# ═══════════════════════════════════════════════════════════════
# FINANCIAL TOOLS (Phase 2 — table created now)
# ═══════════════════════════════════════════════════════════════

class ToolRunRow(Base):
    """Stores every financial tool execution for audit + replay."""
    __tablename__ = "tool_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    outputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    execution_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        Index("ix_tool_runs_ws_tool", "workspace_id", "tool_name"),
    )


# ═══════════════════════════════════════════════════════════════
# EGM DATA LAYER (Phase 3 — tables created now)
# ═══════════════════════════════════════════════════════════════

class DataSourceRow(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, default="")
    format: Mapped[str] = mapped_column(String(32), default="csv")
    frequency: Mapped[str] = mapped_column(String(32), default="monthly")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EGMLocationRow(Base):
    __tablename__ = "egm_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("data_sources.id"), nullable=False
    )
    source_location_id: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(500), default="")
    municipality: Mapped[str] = mapped_column(String(128), default="")
    county: Mapped[str] = mapped_column(String(128), default="")
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    venue_type: Mapped[str] = mapped_column(String(64), default="other")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    license_number: Mapped[str] = mapped_column(String(64), default="")
    terminal_operator: Mapped[str] = mapped_column(String(255), default="")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_egm_loc_source", "data_source_id", "source_location_id"),
        Index("ix_egm_loc_state_type", "state", "venue_type"),
    )


class EGMMonthlyPerformanceRow(Base):
    __tablename__ = "egm_monthly_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("egm_locations.id"), nullable=False
    )
    data_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("data_sources.id"), nullable=False
    )
    report_month: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False  # First day of month
    )
    terminal_count: Mapped[int] = mapped_column(Integer, default=0)
    coin_in: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    coin_out: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    net_win: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    hold_pct: Mapped[float] = mapped_column(Numeric(8, 6), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    __table_args__ = (
        UniqueConstraint("location_id", "report_month", name="uq_loc_month"),
        Index("ix_egm_perf_month", "report_month"),
        Index("ix_egm_perf_loc", "location_id"),
    )


class IngestRunRow(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("data_sources.id"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String(64), default="")
    run_type: Mapped[str] = mapped_column(String(32), default="manual")
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)
    rows_errored: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    triggered_by: Mapped[str] = mapped_column(String(64), default="")


class IngestErrorRow(Base):
    __tablename__ = "ingest_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingest_runs.id"), nullable=False
    )
    row_num: Mapped[int] = mapped_column(Integer, default=0)
    source_column: Mapped[str] = mapped_column(String(128), default="")
    error_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    raw_row: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


# ═══════════════════════════════════════════════════════════════
# PHASE 4 — FORECASTER TABLES
# ═══════════════════════════════════════════════════════════════

class ModelRegistryRow(Base):
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    training_data_range: Mapped[str] = mapped_column(String(64), default="")
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    artifact_path: Mapped[str] = mapped_column(Text, default="")
    is_champion: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promoted_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_model_version"),
        Index("ix_model_champion", "model_name", "is_champion"),
    )


class PredictionLogRow(Base):
    __tablename__ = "prediction_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    model_version: Mapped[int] = mapped_column(Integer, default=0)
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
    predictions: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    execution_ms: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_pred_workspace", "workspace_id"),
        Index("ix_pred_model", "model_name"),
    )


# ═══════════════════════════════════════════════════════════════
# PHASE 5 — CONTRACT ENGINE TABLES
# ═══════════════════════════════════════════════════════════════

class ContractTemplateRow(Base):
    __tablename__ = "contract_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    agreement_type: Mapped[str] = mapped_column(
        String(64), nullable=False  # 'revenue_share', 'flat_lease', 'hybrid'
    )
    acquisition_type: Mapped[str] = mapped_column(
        String(64), default="cash"  # 'cash', 'financed'
    )
    terms: Mapped[dict] = mapped_column(JSONB, default=dict)
    constraints: Mapped[dict] = mapped_column(JSONB, default=dict)
    state_applicability: Mapped[str] = mapped_column(
        String(128), default=""  # Comma-separated: "IL,NV,PA"
    )
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_ct_workspace", "workspace_id"),
        UniqueConstraint("workspace_id", "name", "version", name="uq_ct_name_ver"),
    )


class SimulationRunRow(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scenario_name: Mapped[str] = mapped_column(String(255), default="")
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict)
    results: Mapped[dict] = mapped_column(JSONB, default=dict)
    num_simulations: Mapped[int] = mapped_column(Integer, default=10000)
    execution_ms: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_sim_workspace", "workspace_id"),
    )


# ═══════════════════════════════════════════════════════════════
# PHASE 6 — REAL ESTATE DEAL PIPELINE TABLES
# ═══════════════════════════════════════════════════════════════

class PropertyTemplateRow(Base):
    __tablename__ = "property_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    property_type: Mapped[str] = mapped_column(
        String(64), nullable=False  # retail_strip, qsr, gas_station, dollar, shopping_center
    )
    defaults: Mapped[dict] = mapped_column(JSONB, default=dict)
    scoring_weights: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_pt_workspace", "workspace_id"),
    )


class DealRunRow(Base):
    __tablename__ = "deal_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    deal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    property_type: Mapped[str] = mapped_column(String(64), default="")
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="pending"  # pending, running, completed, failed
    )
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    stage_results: Mapped[dict] = mapped_column(JSONB, default=dict)
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    decision: Mapped[str] = mapped_column(String(16), default="")  # GO, HOLD, NO_GO
    decision_rationale: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_deal_workspace", "workspace_id"),
        Index("ix_deal_status", "status"),
    )


# ═══════════════════════════════════════════════════════════════
# PHASE 7 — PORTFOLIO TABLES
# ═══════════════════════════════════════════════════════════════

class PortfolioAssetRow(Base):
    __tablename__ = "portfolio_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), default="")
    property_type: Mapped[str] = mapped_column(String(64), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(8), default="")
    municipality: Mapped[str] = mapped_column(String(128), default="")
    acquisition_date: Mapped[str] = mapped_column(String(10), default="")
    acquisition_cost: Mapped[float] = mapped_column(Float, default=0.0)
    current_value: Mapped[float] = mapped_column(Float, default=0.0)
    ownership_type: Mapped[str] = mapped_column(
        String(32), default="owned"  # 'owned', 'financed', 'leased'
    )
    contract_type: Mapped[str] = mapped_column(
        String(32), default=""  # 'revenue_share', 'flat_lease', 'hybrid'
    )
    has_gaming: Mapped[bool] = mapped_column(Boolean, default=False)
    terminal_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_pa_workspace", "workspace_id"),
        Index("ix_pa_state", "state"),
    )


class PortfolioDebtRow(Base):
    __tablename__ = "portfolio_debt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), default="")
    lender: Mapped[str] = mapped_column(String(255), default="")
    original_balance: Mapped[float] = mapped_column(Float, default=0.0)
    current_balance: Mapped[float] = mapped_column(Float, default=0.0)
    annual_rate: Mapped[float] = mapped_column(Float, default=0.0)
    monthly_payment: Mapped[float] = mapped_column(Float, default=0.0)
    maturity_date: Mapped[str] = mapped_column(String(10), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_pd_asset", "asset_id"),
        Index("ix_pd_workspace", "workspace_id"),
    )


class PortfolioNOIRow(Base):
    __tablename__ = "portfolio_noi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), default="")
    period: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM
    noi_amount: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_pnoi_asset", "asset_id"),
        UniqueConstraint("asset_id", "period", name="uq_noi_asset_period"),
    )


class PortfolioEGMExposureRow(Base):
    __tablename__ = "portfolio_egm_exposure"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), default="")
    egm_location_id: Mapped[int] = mapped_column(Integer, default=0)
    machine_count: Mapped[int] = mapped_column(Integer, default=0)
    monthly_net_win: Mapped[float] = mapped_column(Float, default=0.0)
    contract_type: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (Index("ix_pegm_asset", "asset_id"),)


# ═══════════════════════════════════════════════════════════════
# STRATEGIC INTELLIGENCE LAYER
# ═══════════════════════════════════════════════════════════════

class StrategicScenarioRow(Base):
    """A saved scenario input for strategic analysis."""
    __tablename__ = "strategic_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario_text: Mapped[str] = mapped_column(Text, nullable=False)
    objectives: Mapped[list] = mapped_column(JSONB, default=list)
    constraints: Mapped[list] = mapped_column(JSONB, default=list)
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    template_type: Mapped[str] = mapped_column(String(64), default="general")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        Index("ix_strat_scen_ws", "workspace_id"),
    )


class StrategicRunRow(Base):
    """One execution of the strategic pipeline."""
    __tablename__ = "strategic_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="")
    decision: Mapped[str] = mapped_column(String(16), default="")  # GO/MODIFY/NO_GO
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    outputs_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    stage_routes: Mapped[dict] = mapped_column(JSONB, default=dict)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        Index("ix_strat_run_ws", "workspace_id"),
        Index("ix_strat_run_scen", "scenario_id"),
    )


class StrategicArtifactRow(Base):
    """Artifacts produced by strategic runs (memos, exports)."""
    __tablename__ = "strategic_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)  # pdf/json/csv/md
    path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (
        Index("ix_strat_art_run", "run_id"),
    )


class StrategicStageRouteRow(Base):
    """Phase 7: Per-stage LLM route overrides for strategic pipeline."""
    __tablename__ = "strategic_stage_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_type: Mapped[str] = mapped_column(String(64), default="general")
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    route_tier: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "template_type", "stage_name",
                         name="uq_strat_stage_route"),
        Index("ix_strat_sr_ws", "workspace_id"),
    )
