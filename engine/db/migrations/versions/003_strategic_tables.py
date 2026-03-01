"""Strategic Intelligence Layer tables + stage routes

Revision ID: 003_strategic_tables
Revises: 002_domain_tables
Create Date: 2026-03-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "003_strategic_tables"
down_revision: Union[str, None] = "002_domain_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Strategic Scenarios ──────────────────────────────────
    op.create_table(
        "strategic_scenarios",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("scenario_text", sa.Text, nullable=False),
        sa.Column("objectives", JSONB, server_default="[]"),
        sa.Column("constraints", JSONB, server_default="[]"),
        sa.Column("inputs", JSONB, server_default="{}"),
        sa.Column("template_type", sa.String(64), server_default="general"),
        sa.Column("created_by", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_strat_scen_ws", "strategic_scenarios", ["workspace_id"])

    # ── Strategic Runs ───────────────────────────────────────
    op.create_table(
        "strategic_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("scenario_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), unique=True, nullable=False),
        sa.Column("title", sa.String(255), server_default=""),
        sa.Column("decision", sa.String(16), server_default=""),
        sa.Column("confidence", sa.Float, server_default="0"),
        sa.Column("outputs_json", JSONB, server_default="{}"),
        sa.Column("stage_routes", JSONB, server_default="{}"),
        sa.Column("elapsed_ms", sa.Integer, server_default="0"),
        sa.Column("llm_cost_usd", sa.Float, server_default="0"),
        sa.Column("status", sa.String(32), server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_strat_run_ws", "strategic_runs", ["workspace_id"])
    op.create_index("ix_strat_run_scen", "strategic_runs", ["scenario_id"])

    # ── Strategic Artifacts ──────────────────────────────────
    op.create_table(
        "strategic_artifacts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("path", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_strat_art_run", "strategic_artifacts", ["run_id"])

    # ── Phase 7: Strategic Stage Routes ──────────────────────
    op.create_table(
        "strategic_stage_routes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("template_type", sa.String(64), server_default="general"),
        sa.Column("stage_name", sa.String(64), nullable=False),
        sa.Column("route_tier", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, server_default="true"),
    )
    op.create_index("ix_strat_sr_ws", "strategic_stage_routes", ["workspace_id"])
    op.create_unique_constraint(
        "uq_strat_stage_route", "strategic_stage_routes",
        ["workspace_id", "template_type", "stage_name"],
    )


def downgrade() -> None:
    op.drop_table("strategic_stage_routes")
    op.drop_table("strategic_artifacts")
    op.drop_table("strategic_runs")
    op.drop_table("strategic_scenarios")
