"""
engine.financial.policies — PolicyBroker Registration
=======================================================
Phase 2: Auto-register financial tools with the PolicyBroker.

All financial tools are pure-compute (no filesystem, no network),
so they get permissive policies: read+compute scope, no egress,
auto-approval, generous rate limits.

Usage:
    from engine.financial.policies import register_financial_policies

    # With in-memory PolicyBroker
    register_financial_policies(broker)

    # With DB-backed ACP (Phase 1)
    register_financial_policies_db(session, workspace_id)
"""

from __future__ import annotations

from typing import Optional

from engine.financial.tools import FINANCIAL_TOOLS


def register_financial_policies(broker) -> int:
    """Register all financial tools with an in-memory PolicyBroker.

    Args:
        broker: engine.policy.PolicyBroker instance

    Returns:
        Number of policies registered
    """
    from engine.policy import ToolPolicy, ActionScope, ApprovalRequirement

    count = 0
    for tool_name, tool_def in FINANCIAL_TOOLS.items():
        policy = ToolPolicy(
            tool_name=tool_name,
            description=tool_def["description"],
            allowed_scopes=[ActionScope.READ.value, ActionScope.EXECUTE.value],
            path_allowlist=[],         # No filesystem access
            allowed_domains=[],        # No network access
            allow_egress=False,
            approval=ApprovalRequirement.AUTO.value,
            max_calls_per_stage=100,   # Generous — these are fast
            max_calls_per_pipeline=500,
        )
        broker.register_policy(policy)
        count += 1
    return count


def register_financial_policies_db(
    session, workspace_id: str, user_id: str = "system",
) -> int:
    """Register financial tool policies in the database (ACP).

    Creates tool_policy rows for each financial tool if they
    don't already exist. Idempotent.

    Args:
        session: SQLAlchemy session
        workspace_id: Target workspace
        user_id: Who is creating the policies

    Returns:
        Number of policies created (0 if all exist)
    """
    from engine.db.acp_repositories import ToolPolicyRepo

    repo = ToolPolicyRepo(session)
    existing = repo.list_by_workspace(workspace_id)
    existing_tools = {p["tool_name"] for p in existing}

    count = 0
    for tool_name, tool_def in FINANCIAL_TOOLS.items():
        if tool_name in existing_tools:
            continue

        repo.create(workspace_id, {
            "tool_name": tool_name,
            "agent_name": "*",           # Available to all agents
            "action_scope": "execute",
            "rate_limit_per_min": 120,   # 2/sec — these are sub-ms tools
            "rate_limit_per_run": 500,
            "requires_approval": False,
            "egress_allowed_domains": [],
            "enabled": True,
        })
        count += 1

    return count
