"""Initial schema — platform core, ACP, financial tools, EGM data layer

Revision ID: 001_initial
Revises:
Create Date: 2026-02-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Platform Core ────────────────────────────────────────

    op.create_table(
        "organizations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.String(64), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("settings", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("name", sa.String(255), server_default=""),
        sa.Column("org_id", sa.String(64), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("password_hash", sa.String(255), server_default=""),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_user_workspace"),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("key_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scopes", JSONB, server_default="[]"),
        sa.Column("revoked", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_hash", "api_keys", ["key_hash"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), server_default=""),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(255), server_default=""),
        sa.Column("outcome", sa.String(32), server_default=""),
        sa.Column("details", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_ws_action", "audit_logs", ["workspace_id", "action"])
    op.create_index("ix_audit_ws_time", "audit_logs", ["workspace_id", "created_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("pipeline_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued"),
        sa.Column("config", JSONB, server_default="{}"),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_ws_status", "jobs", ["workspace_id", "status"])

    # ── Agent Control Plane ──────────────────────────────────

    op.create_table(
        "agent_configs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("model_provider", sa.String(64), server_default="openai"),
        sa.Column("model_name", sa.String(128), server_default="gpt-4o"),
        sa.Column("max_tokens", sa.Integer, server_default="128000"),
        sa.Column("temperature", sa.Float, server_default="0.5"),
        sa.Column("timeout_sec", sa.Integer, server_default="300"),
        sa.Column("retry_count", sa.Integer, server_default="3"),
        sa.Column("retry_backoff", sa.Float, server_default="2.0"),
        sa.Column("tool_allowlist", JSONB, server_default="[]"),
        sa.Column("prompt_template", sa.Text, server_default=""),
        sa.Column("output_schema", JSONB, nullable=True),
        sa.Column("agent_weight", sa.Float, server_default="1.0"),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), server_default=""),
        sa.UniqueConstraint("workspace_id", "agent_name", name="uq_ws_agent"),
    )
    op.create_index("ix_agent_configs_ws", "agent_configs", ["workspace_id"])

    op.create_table(
        "agent_config_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_config_id", sa.Integer, sa.ForeignKey("agent_configs.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("config_snapshot", JSONB, nullable=False),
        sa.Column("changed_by", sa.String(64), server_default=""),
        sa.Column("change_reason", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_acv_config_version", "agent_config_versions", ["agent_config_id", "version"])

    op.create_table(
        "model_routes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("primary_provider", sa.String(64), nullable=False),
        sa.Column("primary_model", sa.String(128), nullable=False),
        sa.Column("fallback_provider", sa.String(64), server_default=""),
        sa.Column("fallback_model", sa.String(128), server_default=""),
        sa.Column("cost_cap_per_run", sa.Float, server_default="10.0"),
        sa.Column("latency_cap_ms", sa.Integer, server_default="30000"),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.UniqueConstraint("workspace_id", "tier", name="uq_ws_tier"),
    )

    op.create_table(
        "tool_policies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("agent_name", sa.String(128), server_default="*"),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("action_scope", sa.String(32), server_default="read"),
        sa.Column("rate_limit_per_min", sa.Integer, server_default="60"),
        sa.Column("rate_limit_per_run", sa.Integer, server_default="100"),
        sa.Column("requires_approval", sa.Boolean, server_default="false"),
        sa.Column("egress_allowed_domains", JSONB, server_default="[]"),
        sa.Column("enabled", sa.Boolean, server_default="true"),
    )
    op.create_index("ix_tool_policies_ws", "tool_policies", ["workspace_id"])

    op.create_table(
        "pipeline_defs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("stages", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "name", name="uq_ws_pipeline"),
    )

    op.create_table(
        "strategy_weights",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("mode_a_capital_filter", sa.Float, server_default="0.25"),
        sa.Column("mode_b_vertical_integration", sa.Float, server_default="0.25"),
        sa.Column("mode_c_regional_empire", sa.Float, server_default="0.25"),
        sa.Column("mode_d_opportunistic", sa.Float, server_default="0.25"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), server_default=""),
    )

    # ── Financial Tools ──────────────────────────────────────

    op.create_table(
        "tool_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), server_default=""),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("inputs", JSONB, nullable=False),
        sa.Column("outputs", JSONB, nullable=False),
        sa.Column("execution_ms", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tool_runs_ws_tool", "tool_runs", ["workspace_id", "tool_name"])

    # ── EGM Data Layer ───────────────────────────────────────

    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("url", sa.Text, server_default=""),
        sa.Column("format", sa.String(32), server_default="csv"),
        sa.Column("frequency", sa.String(32), server_default="monthly"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean, server_default="true"),
    )

    op.create_table(
        "egm_locations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("data_source_id", sa.Integer, sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("source_location_id", sa.String(128), server_default=""),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(500), server_default=""),
        sa.Column("municipality", sa.String(128), server_default=""),
        sa.Column("county", sa.String(128), server_default=""),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("venue_type", sa.String(64), server_default="other"),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column("license_number", sa.String(64), server_default=""),
        sa.Column("terminal_operator", sa.String(255), server_default=""),
        sa.Column("attributes", JSONB, server_default="{}"),
        sa.Column("first_seen_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true"),
    )
    op.create_index("ix_egm_loc_source", "egm_locations", ["data_source_id", "source_location_id"])
    op.create_index("ix_egm_loc_state_type", "egm_locations", ["state", "venue_type"])

    op.create_table(
        "egm_monthly_performance",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("location_id", sa.Integer, sa.ForeignKey("egm_locations.id"), nullable=False),
        sa.Column("data_source_id", sa.Integer, sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("report_month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_count", sa.Integer, server_default="0"),
        sa.Column("coin_in", sa.Numeric(14, 2), server_default="0"),
        sa.Column("coin_out", sa.Numeric(14, 2), server_default="0"),
        sa.Column("net_win", sa.Numeric(14, 2), server_default="0"),
        sa.Column("hold_pct", sa.Numeric(8, 6), server_default="0"),
        sa.Column("tax_amount", sa.Numeric(12, 2), server_default="0"),
        sa.UniqueConstraint("location_id", "report_month", name="uq_loc_month"),
    )
    op.create_index("ix_egm_perf_month", "egm_monthly_performance", ["report_month"])
    op.create_index("ix_egm_perf_loc", "egm_monthly_performance", ["location_id"])

    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("data_source_id", sa.Integer, sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("workspace_id", sa.String(64), server_default=""),
        sa.Column("run_type", sa.String(32), server_default="manual"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("rows_processed", sa.Integer, server_default="0"),
        sa.Column("rows_inserted", sa.Integer, server_default="0"),
        sa.Column("rows_updated", sa.Integer, server_default="0"),
        sa.Column("rows_errored", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(64), server_default=""),
    )

    op.create_table(
        "ingest_errors",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ingest_run_id", sa.Integer, sa.ForeignKey("ingest_runs.id"), nullable=False),
        sa.Column("row_num", sa.Integer, server_default="0"),
        sa.Column("source_column", sa.String(128), server_default=""),
        sa.Column("error_type", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text, server_default=""),
        sa.Column("raw_row", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ingest_errors")
    op.drop_table("ingest_runs")
    op.drop_table("egm_monthly_performance")
    op.drop_table("egm_locations")
    op.drop_table("data_sources")
    op.drop_table("tool_runs")
    op.drop_table("strategy_weights")
    op.drop_table("pipeline_defs")
    op.drop_table("tool_policies")
    op.drop_table("model_routes")
    op.drop_table("agent_config_versions")
    op.drop_table("agent_configs")
    op.drop_table("jobs")
    op.drop_table("audit_logs")
    op.drop_table("api_keys")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("workspaces")
    op.drop_table("organizations")
