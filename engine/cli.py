"""
engine.cli — Command-Line Interface
=======================================
Canonical entrypoint for the AgenticEngine.

Usage:
    agentic-engine serve      # Start API server
    agentic-engine migrate    # Run Alembic migrations
    agentic-engine seed       # Seed default templates
    agentic-engine check      # Health check
"""

from __future__ import annotations

import argparse
import logging
import sys


def cmd_serve(args):
    """Start the FastAPI server."""
    import uvicorn
    from engine.db.settings import get_settings

    settings = get_settings()
    host = args.host or settings.api_host
    port = args.port or settings.api_port

    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )

    print(f"Starting AgenticEngine on {host}:{port} (env={settings.env})")
    uvicorn.run(
        "engine.api_server:app",
        host=host,
        port=port,
        reload=settings.is_development,
    )


def cmd_migrate(args):
    """Run Alembic database migrations."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("alembic.ini")

    # Override URL from environment
    try:
        from engine.db.settings import get_settings
        settings = get_settings()
        alembic_cfg.set_main_option("sqlalchemy.url", settings.db.dsn)
    except Exception:
        pass  # Fall back to alembic.ini URL

    if args.revision:
        print(f"Migrating to revision: {args.revision}")
        command.upgrade(alembic_cfg, args.revision)
    else:
        print("Migrating to head...")
        command.upgrade(alembic_cfg, "head")

    print("✓ Migrations complete")


def cmd_seed(args):
    """Seed default templates into the database."""
    from engine.db.session import get_session

    with get_session() as session:
        # Seed contract templates
        from engine.contracts.templates import default_templates
        from engine.db.contract_repositories import ContractTemplateRepo

        ct_repo = ContractTemplateRepo(session)
        templates = default_templates()
        created = 0
        for tmpl in templates:
            try:
                ct_repo.create(
                    workspace_id=args.workspace or "default",
                    name=tmpl["name"],
                    agreement_type=tmpl["agreement_type"],
                    acquisition_type=tmpl.get("acquisition_type", "cash"),
                    terms=tmpl.get("terms", {}),
                    constraints=tmpl.get("constraints", {}),
                )
                created += 1
            except Exception:
                pass  # Already exists
        print(f"✓ Seeded {created} contract templates")

        # Seed property templates
        from engine.realestate.templates import default_property_templates
        from engine.db.deal_repositories import PropertyTemplateRepo

        pt_repo = PropertyTemplateRepo(session)
        props = default_property_templates()
        p_created = 0
        for p in props:
            try:
                pt_repo.create(
                    workspace_id=args.workspace or "default",
                    name=p["name"],
                    property_type=p["property_type"],
                    defaults=p.get("defaults", {}),
                    scoring_weights=p.get("scoring_weights", {}),
                )
                p_created += 1
            except Exception:
                pass
        print(f"✓ Seeded {p_created} property templates")

        # Seed strategic agents (Phase 2)
        _seed_strategic_agents(session, args.workspace or "default")

        # Seed strategic stage routes (Phase 7)
        _seed_strategic_stage_routes(session, args.workspace or "default")

        session.commit()
    print("✓ Seeding complete")


# ── Strategic Agent Seed Definitions ─────────────────────────

STRATEGIC_AGENTS = [
    {
        "agent_name": "structuring_analyst",
        "model_provider": "anthropic",
        "model_name": "claude-haiku-4-5-20251001",
        "temperature": 0.3,
        "max_tokens": 32000,
        "timeout_sec": 60,
        "tool_allowlist": ["strategic_analyze", "assumption_audit"],
        "prompt_template": "You are a structuring analyst. Decompose scenarios into structured, analyzable components. Identify assumptions, variables, and information gaps.",
    },
    {
        "agent_name": "decision_analyst",
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-4-5-20250929",
        "temperature": 0.4,
        "max_tokens": 64000,
        "timeout_sec": 120,
        "tool_allowlist": ["decision_stress_test", "swot_generate", "evaluate_deal", "portfolio_dashboard"],
        "prompt_template": "You are a decision analyst. Assess decision readiness, identify gating risks, and stress-test proposals against failure modes.",
    },
    {
        "agent_name": "scenario_analyst",
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-4-5-20250929",
        "temperature": 0.6,
        "max_tokens": 64000,
        "timeout_sec": 120,
        "tool_allowlist": ["scenario_simulate", "simulate_contract", "egm_predict", "amortize"],
        "prompt_template": "You are a scenario analyst. Generate bull/base/bear cases, identify sensitivities, and model second-order effects. Use quantitative tools where available.",
    },
    {
        "agent_name": "pattern_analyst",
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-4-5-20250929",
        "temperature": 0.5,
        "max_tokens": 64000,
        "timeout_sec": 120,
        "tool_allowlist": ["decision_stress_test", "portfolio_dashboard", "deal_impact", "egm_market_health"],
        "prompt_template": "You are a pattern analyst. Identify failure modes, leverage points, contradictions, and analogous precedents. Cross-reference with portfolio and market data.",
    },
    {
        "agent_name": "executive_synthesizer",
        "model_provider": "anthropic",
        "model_name": "claude-opus-4-6",
        "temperature": 0.3,
        "max_tokens": 128000,
        "timeout_sec": 300,
        "tool_allowlist": ["strategic_analyze", "swot_generate", "assumption_audit"],
        "prompt_template": "You are an executive synthesizer. Combine all analysis into a clear GO/MODIFY/NO-GO decision with confidence score, SWOT, and actionable next steps. Be direct and concise.",
    },
]

STRATEGIC_TOOL_POLICIES = [
    {"tool_name": "strategic_analyze", "action_scope": "read", "requires_approval": False},
    {"tool_name": "swot_generate", "action_scope": "read", "requires_approval": False},
    {"tool_name": "decision_stress_test", "action_scope": "read", "requires_approval": False},
    {"tool_name": "scenario_simulate", "action_scope": "read", "requires_approval": False},
    {"tool_name": "assumption_audit", "action_scope": "read", "requires_approval": False},
]

STRATEGIC_MODEL_ROUTES = [
    {"tier": "strategic_deep", "primary_provider": "anthropic",
     "primary_model": "claude-sonnet-4-5-20250929",
     "fallback_provider": "openai", "fallback_model": "gpt-4.1",
     "cost_cap_per_run": 5.0, "latency_cap_ms": 60000},
    {"tier": "strategic_fast", "primary_provider": "anthropic",
     "primary_model": "claude-sonnet-4-5-20250929",
     "fallback_provider": "anthropic", "fallback_model": "claude-haiku-4-5-20251001",
     "cost_cap_per_run": 1.0, "latency_cap_ms": 15000},
    {"tier": "cheap_structured", "primary_provider": "anthropic",
     "primary_model": "claude-haiku-4-5-20251001",
     "fallback_provider": "openai", "fallback_model": "gpt-4.1-mini",
     "cost_cap_per_run": 0.50, "latency_cap_ms": 10000},
]


def _seed_strategic_agents(session, workspace_id: str):
    """Seed the 5 strategic agents + tool policies + model routes."""
    from engine.db.models import AgentConfigRow, ToolPolicyRow, ModelRouteRow

    created = 0
    for agent in STRATEGIC_AGENTS:
        exists = (
            session.query(AgentConfigRow)
            .filter_by(workspace_id=workspace_id, agent_name=agent["agent_name"])
            .first()
        )
        if not exists:
            session.add(AgentConfigRow(workspace_id=workspace_id, **agent))
            created += 1
    print(f"✓ Seeded {created} strategic agents")

    # Tool policies (deny-by-default compliant: READ only)
    pol_created = 0
    for pol in STRATEGIC_TOOL_POLICIES:
        exists = (
            session.query(ToolPolicyRow)
            .filter_by(workspace_id=workspace_id, tool_name=pol["tool_name"], agent_name="*")
            .first()
        )
        if not exists:
            session.add(ToolPolicyRow(workspace_id=workspace_id, agent_name="*", **pol))
            pol_created += 1
    print(f"✓ Seeded {pol_created} strategic tool policies")

    # Model routes
    route_created = 0
    for route in STRATEGIC_MODEL_ROUTES:
        exists = (
            session.query(ModelRouteRow)
            .filter_by(workspace_id=workspace_id, tier=route["tier"])
            .first()
        )
        if not exists:
            session.add(ModelRouteRow(workspace_id=workspace_id, **route))
            route_created += 1
    print(f"✓ Seeded {route_created} strategic model routes")


def _seed_strategic_stage_routes(session, workspace_id: str):
    """Seed default stage-to-route mappings for all templates (Phase 7)."""
    from engine.db.models import StrategicStageRouteRow
    from engine.strategic.templates import default_scenario_templates

    created = 0
    for tmpl in default_scenario_templates():
        template_type = tmpl["template_type"]
        routes = tmpl.get("stage_routes", {})
        for stage_name, route_tier in routes.items():
            exists = (
                session.query(StrategicStageRouteRow)
                .filter_by(
                    workspace_id=workspace_id,
                    template_type=template_type,
                    stage_name=stage_name,
                )
                .first()
            )
            if not exists:
                session.add(StrategicStageRouteRow(
                    workspace_id=workspace_id,
                    template_type=template_type,
                    stage_name=stage_name,
                    route_tier=route_tier,
                ))
                created += 1
    print(f"✓ Seeded {created} strategic stage routes (Phase 7)")


def cmd_check(args):
    """Health check against the running server."""
    import urllib.request
    import json

    host = args.host or "localhost"
    port = args.port or 8000
    url = f"http://{host}:{port}/health"

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            status = data.get("status", "unknown")
            version = data.get("version", "?")
            print(f"✓ AgenticEngine v{version} — {status}")
            sys.exit(0)
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="agentic-engine",
        description="AgenticEngine CLI — enterprise agentic AI pipeline engine",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # serve
    p_serve = sub.add_parser("serve", help="Start API server")
    p_serve.add_argument("--host", default=None, help="Bind host (default: from settings)")
    p_serve.add_argument("--port", type=int, default=None, help="Bind port (default: from settings)")

    # migrate
    p_migrate = sub.add_parser("migrate", help="Run database migrations")
    p_migrate.add_argument("--revision", default=None, help="Target revision (default: head)")

    # seed
    p_seed = sub.add_parser("seed", help="Seed default templates")
    p_seed.add_argument("--workspace", default="default", help="Workspace ID")

    # check
    p_check = sub.add_parser("check", help="Health check against running server")
    p_check.add_argument("--host", default="localhost")
    p_check.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "serve": cmd_serve,
        "migrate": cmd_migrate,
        "seed": cmd_seed,
        "check": cmd_check,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
