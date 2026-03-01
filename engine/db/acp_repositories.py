"""
engine.db.acp_repositories — Agent Control Plane Repositories
================================================================
Phase 1: Repositories for all ACP tables.

These repos manage the DB-backed configuration that drives
every agent, model route, tool policy, pipeline, and strategy
in the system. All reads go through Redis cache (see cache.py).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, delete, and_, func
from sqlalchemy.orm import Session

from engine.db.models import (
    ModelRouteRow, ToolPolicyRow, PipelineDefRow, StrategyWeightsRow,
)


# ═══════════════════════════════════════════════════════════════
# MODEL ROUTE REPO
# ═══════════════════════════════════════════════════════════════

class ModelRouteRepo:
    """Manages tiered model routing config per workspace."""

    def __init__(self, session: Session):
        self.session = session

    def upsert(self, workspace_id: str, tier: str, config: Dict[str, Any]) -> Dict:
        existing = self.session.execute(
            select(ModelRouteRow).where(
                and_(
                    ModelRouteRow.workspace_id == workspace_id,
                    ModelRouteRow.tier == tier,
                )
            )
        ).scalar_one_or_none()

        if existing:
            for key, val in config.items():
                if hasattr(existing, key) and key not in ("id", "workspace_id", "tier"):
                    setattr(existing, key, val)
            self.session.flush()
            return self._to_dict(existing)
        else:
            row = ModelRouteRow(
                workspace_id=workspace_id,
                tier=tier,
                **{k: v for k, v in config.items()
                   if hasattr(ModelRouteRow, k) and k not in ("id", "workspace_id", "tier")},
            )
            self.session.add(row)
            self.session.flush()
            return self._to_dict(row)

    def get(self, workspace_id: str, tier: str) -> Optional[Dict]:
        row = self.session.execute(
            select(ModelRouteRow).where(
                and_(
                    ModelRouteRow.workspace_id == workspace_id,
                    ModelRouteRow.tier == tier,
                )
            )
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def list_by_workspace(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(ModelRouteRow).where(
                ModelRouteRow.workspace_id == workspace_id
            )
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def delete(self, workspace_id: str, tier: str) -> bool:
        result = self.session.execute(
            delete(ModelRouteRow).where(
                and_(
                    ModelRouteRow.workspace_id == workspace_id,
                    ModelRouteRow.tier == tier,
                )
            )
        )
        self.session.flush()
        return result.rowcount > 0

    def _to_dict(self, row: ModelRouteRow) -> Dict:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "tier": row.tier,
            "primary_provider": row.primary_provider,
            "primary_model": row.primary_model,
            "fallback_provider": row.fallback_provider,
            "fallback_model": row.fallback_model,
            "cost_cap_per_run": row.cost_cap_per_run,
            "latency_cap_ms": row.latency_cap_ms,
            "enabled": row.enabled,
        }


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY REPO
# ═══════════════════════════════════════════════════════════════

class ToolPolicyRepo:
    """Manages tool-level policies per workspace/agent."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, workspace_id: str, config: Dict[str, Any]) -> Dict:
        row = ToolPolicyRow(
            workspace_id=workspace_id,
            **{k: v for k, v in config.items()
               if hasattr(ToolPolicyRow, k) and k not in ("id", "workspace_id")},
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def update(self, policy_id: int, config: Dict[str, Any]) -> Optional[Dict]:
        row = self.session.get(ToolPolicyRow, policy_id)
        if not row:
            return None
        for key, val in config.items():
            if hasattr(row, key) and key not in ("id", "workspace_id"):
                setattr(row, key, val)
        self.session.flush()
        return self._to_dict(row)

    def get(self, policy_id: int) -> Optional[Dict]:
        row = self.session.get(ToolPolicyRow, policy_id)
        return self._to_dict(row) if row else None

    def list_by_workspace(
        self, workspace_id: str, agent_name: Optional[str] = None,
    ) -> List[Dict]:
        stmt = select(ToolPolicyRow).where(
            ToolPolicyRow.workspace_id == workspace_id
        )
        if agent_name:
            stmt = stmt.where(
                ToolPolicyRow.agent_name.in_([agent_name, "*"])
            )
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_dict(r) for r in rows]

    def get_for_tool(
        self, workspace_id: str, tool_name: str,
        agent_name: str = "*",
    ) -> Optional[Dict]:
        """Get the most specific policy for a tool.

        Priority: agent-specific > wildcard (*)
        """
        rows = self.session.execute(
            select(ToolPolicyRow).where(
                and_(
                    ToolPolicyRow.workspace_id == workspace_id,
                    ToolPolicyRow.tool_name == tool_name,
                    ToolPolicyRow.agent_name.in_([agent_name, "*"]),
                    ToolPolicyRow.enabled == True,
                )
            ).order_by(
                # Agent-specific first, then wildcard
                ToolPolicyRow.agent_name.desc()
            )
        ).scalars().all()

        if not rows:
            return None
        # Prefer agent-specific over wildcard
        for r in rows:
            if r.agent_name == agent_name:
                return self._to_dict(r)
        return self._to_dict(rows[0])

    def delete(self, policy_id: int) -> bool:
        result = self.session.execute(
            delete(ToolPolicyRow).where(ToolPolicyRow.id == policy_id)
        )
        self.session.flush()
        return result.rowcount > 0

    def _to_dict(self, row: ToolPolicyRow) -> Dict:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "agent_name": row.agent_name,
            "tool_name": row.tool_name,
            "action_scope": row.action_scope,
            "rate_limit_per_min": row.rate_limit_per_min,
            "rate_limit_per_run": row.rate_limit_per_run,
            "requires_approval": row.requires_approval,
            "egress_allowed_domains": row.egress_allowed_domains,
            "enabled": row.enabled,
        }


# ═══════════════════════════════════════════════════════════════
# PIPELINE DEF REPO
# ═══════════════════════════════════════════════════════════════

class PipelineDefRepo:
    """Manages pipeline definitions (DAG stage configs) per workspace."""

    def __init__(self, session: Session):
        self.session = session

    def upsert(
        self, workspace_id: str, name: str,
        stages: List[Dict], changed_by: str = "",
    ) -> Dict:
        existing = self.session.execute(
            select(PipelineDefRow).where(
                and_(
                    PipelineDefRow.workspace_id == workspace_id,
                    PipelineDefRow.name == name,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.stages = stages
            existing.version += 1
            self.session.flush()
            return self._to_dict(existing)
        else:
            row = PipelineDefRow(
                workspace_id=workspace_id,
                name=name,
                stages=stages,
                version=1,
            )
            self.session.add(row)
            self.session.flush()
            return self._to_dict(row)

    def get(self, workspace_id: str, name: str) -> Optional[Dict]:
        row = self.session.execute(
            select(PipelineDefRow).where(
                and_(
                    PipelineDefRow.workspace_id == workspace_id,
                    PipelineDefRow.name == name,
                )
            )
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def list_by_workspace(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(PipelineDefRow).where(
                PipelineDefRow.workspace_id == workspace_id
            )
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def delete(self, workspace_id: str, name: str) -> bool:
        result = self.session.execute(
            delete(PipelineDefRow).where(
                and_(
                    PipelineDefRow.workspace_id == workspace_id,
                    PipelineDefRow.name == name,
                )
            )
        )
        self.session.flush()
        return result.rowcount > 0

    def _to_dict(self, row: PipelineDefRow) -> Dict:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "version": row.version,
            "stages": row.stages,
            "enabled": row.enabled,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }


# ═══════════════════════════════════════════════════════════════
# STRATEGY WEIGHTS REPO
# ═══════════════════════════════════════════════════════════════

class StrategyWeightsRepo:
    """Manages investment strategy mode weights per workspace."""

    def __init__(self, session: Session):
        self.session = session

    def set_weights(
        self, workspace_id: str, weights: Dict[str, float],
        created_by: str = "",
    ) -> Dict:
        """Create a new version of strategy weights (append-only)."""
        # Get current max version
        max_v = self.session.execute(
            select(func.max(StrategyWeightsRow.version)).where(
                StrategyWeightsRow.workspace_id == workspace_id
            )
        ).scalar_one_or_none() or 0

        row = StrategyWeightsRow(
            workspace_id=workspace_id,
            version=max_v + 1,
            mode_a_capital_filter=weights.get("mode_a_capital_filter", 0.25),
            mode_b_vertical_integration=weights.get("mode_b_vertical_integration", 0.25),
            mode_c_regional_empire=weights.get("mode_c_regional_empire", 0.25),
            mode_d_opportunistic=weights.get("mode_d_opportunistic", 0.25),
            created_by=created_by,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get_current(self, workspace_id: str) -> Optional[Dict]:
        """Get the latest version of strategy weights."""
        row = self.session.execute(
            select(StrategyWeightsRow)
            .where(StrategyWeightsRow.workspace_id == workspace_id)
            .order_by(StrategyWeightsRow.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def get_version(self, workspace_id: str, version: int) -> Optional[Dict]:
        row = self.session.execute(
            select(StrategyWeightsRow).where(
                and_(
                    StrategyWeightsRow.workspace_id == workspace_id,
                    StrategyWeightsRow.version == version,
                )
            )
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def list_versions(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(StrategyWeightsRow)
            .where(StrategyWeightsRow.workspace_id == workspace_id)
            .order_by(StrategyWeightsRow.version.desc())
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: StrategyWeightsRow) -> Dict:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "version": row.version,
            "mode_a_capital_filter": row.mode_a_capital_filter,
            "mode_b_vertical_integration": row.mode_b_vertical_integration,
            "mode_c_regional_empire": row.mode_c_regional_empire,
            "mode_d_opportunistic": row.mode_d_opportunistic,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "created_by": row.created_by,
        }
