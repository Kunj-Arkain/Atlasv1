"""Phase 4-7 tables — forecasting, contracts, deals, portfolio

Revision ID: 002_domain_tables
Revises: 001_initial
Create Date: 2026-03-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002_domain_tables"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Phase 4: EGM Forecasting ─────────────────────────────

    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("model_type", sa.String(64), nullable=False),
        sa.Column("training_data_range", sa.String(64), server_default=""),
        sa.Column("metrics", JSONB, server_default="{}"),
        sa.Column("parameters", JSONB, server_default="{}"),
        sa.Column("artifact_path", sa.Text, server_default=""),
        sa.Column("is_champion", sa.Boolean, server_default="false"),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_by", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_name", "version", name="uq_model_version"),
    )
    op.create_index("ix_model_champion", "model_registry", ["model_name", "is_champion"])

    op.create_table(
        "prediction_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), server_default=""),
        sa.Column("model_name", sa.String(128), server_default=""),
        sa.Column("model_version", sa.Integer, server_default="0"),
        sa.Column("inputs", JSONB, server_default="{}"),
        sa.Column("features", JSONB, server_default="{}"),
        sa.Column("predictions", JSONB, server_default="{}"),
        sa.Column("confidence", sa.Float, server_default="0"),
        sa.Column("execution_ms", sa.Integer, server_default="0"),
        sa.Column("user_id", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pred_workspace", "prediction_log", ["workspace_id"])
    op.create_index("ix_pred_model", "prediction_log", ["model_name"])

    # ── Phase 5: Contract Engine ─────────────────────────────

    op.create_table(
        "contract_templates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("agreement_type", sa.String(64), nullable=False),
        sa.Column("acquisition_type", sa.String(64), server_default="cash"),
        sa.Column("terms", JSONB, server_default="{}"),
        sa.Column("constraints", JSONB, server_default="{}"),
        sa.Column("state_applicability", sa.String(128), server_default=""),
        sa.Column("approval_required", sa.Boolean, server_default="false"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_by", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "name", "version", name="uq_ct_name_ver"),
    )
    op.create_index("ix_ct_workspace", "contract_templates", ["workspace_id"])

    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("template_id", sa.Integer, nullable=True),
        sa.Column("scenario_name", sa.String(255), server_default=""),
        sa.Column("inputs", JSONB, server_default="{}"),
        sa.Column("overrides", JSONB, server_default="{}"),
        sa.Column("results", JSONB, server_default="{}"),
        sa.Column("num_simulations", sa.Integer, server_default="10000"),
        sa.Column("execution_ms", sa.Integer, server_default="0"),
        sa.Column("user_id", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sim_workspace", "simulation_runs", ["workspace_id"])

    # ── Phase 6: Real Estate Deal Pipeline ───────────────────

    op.create_table(
        "property_templates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("property_type", sa.String(64), nullable=False),
        sa.Column("defaults", JSONB, server_default="{}"),
        sa.Column("scoring_weights", JSONB, server_default="{}"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pt_workspace", "property_templates", ["workspace_id"])

    op.create_table(
        "deal_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("deal_name", sa.String(255), nullable=False),
        sa.Column("property_type", sa.String(64), server_default=""),
        sa.Column("template_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("inputs", JSONB, server_default="{}"),
        sa.Column("stage_results", JSONB, server_default="{}"),
        sa.Column("scores", JSONB, server_default="{}"),
        sa.Column("decision", sa.String(16), server_default=""),
        sa.Column("decision_rationale", sa.Text, server_default=""),
        sa.Column("user_id", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_deal_workspace", "deal_runs", ["workspace_id"])
    op.create_index("ix_deal_status", "deal_runs", ["status"])

    # ── Phase 7: Portfolio ───────────────────────────────────

    op.create_table(
        "portfolio_assets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("asset_type", sa.String(64), server_default=""),
        sa.Column("property_type", sa.String(64), server_default=""),
        sa.Column("address", sa.Text, server_default=""),
        sa.Column("state", sa.String(8), server_default=""),
        sa.Column("municipality", sa.String(128), server_default=""),
        sa.Column("acquisition_date", sa.String(10), server_default=""),
        sa.Column("acquisition_cost", sa.Float, server_default="0"),
        sa.Column("current_value", sa.Float, server_default="0"),
        sa.Column("ownership_type", sa.String(32), server_default="owned"),
        sa.Column("contract_type", sa.String(32), server_default=""),
        sa.Column("has_gaming", sa.Boolean, server_default="false"),
        sa.Column("terminal_count", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("metadata_json", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pa_workspace", "portfolio_assets", ["workspace_id"])
    op.create_index("ix_pa_state", "portfolio_assets", ["state"])

    op.create_table(
        "portfolio_debt",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("workspace_id", sa.String(64), server_default=""),
        sa.Column("lender", sa.String(255), server_default=""),
        sa.Column("original_balance", sa.Float, server_default="0"),
        sa.Column("current_balance", sa.Float, server_default="0"),
        sa.Column("annual_rate", sa.Float, server_default="0"),
        sa.Column("monthly_payment", sa.Float, server_default="0"),
        sa.Column("maturity_date", sa.String(10), server_default=""),
        sa.Column("is_active", sa.Boolean, server_default="true"),
    )
    op.create_index("ix_pd_asset", "portfolio_debt", ["asset_id"])
    op.create_index("ix_pd_workspace", "portfolio_debt", ["workspace_id"])

    op.create_table(
        "portfolio_noi",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("workspace_id", sa.String(64), server_default=""),
        sa.Column("period", sa.String(10), server_default=""),
        sa.Column("noi_amount", sa.Float, server_default="0"),
        sa.UniqueConstraint("asset_id", "period", name="uq_noi_asset_period"),
    )
    op.create_index("ix_pnoi_asset", "portfolio_noi", ["asset_id"])

    op.create_table(
        "portfolio_egm_exposure",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.Integer, nullable=False),
        sa.Column("workspace_id", sa.String(64), server_default=""),
        sa.Column("egm_location_id", sa.Integer, server_default="0"),
        sa.Column("machine_count", sa.Integer, server_default="0"),
        sa.Column("monthly_net_win", sa.Float, server_default="0"),
        sa.Column("contract_type", sa.String(32), server_default=""),
    )
    op.create_index("ix_pegm_asset", "portfolio_egm_exposure", ["asset_id"])


def downgrade() -> None:
    op.drop_table("portfolio_egm_exposure")
    op.drop_table("portfolio_noi")
    op.drop_table("portfolio_debt")
    op.drop_table("portfolio_assets")
    op.drop_table("deal_runs")
    op.drop_table("property_templates")
    op.drop_table("simulation_runs")
    op.drop_table("contract_templates")
    op.drop_table("prediction_log")
    op.drop_table("model_registry")
