"""
engine.acp — Agent Control Plane
====================================
Phase 1: DB-backed adapters that replace in-memory config
with live config reads from Postgres (via Redis cache).

These adapters implement the same interfaces as the existing
engine classes but read all configuration from the database.

Key classes:
  - ACPLLMRouter:      Replaces observability.LLMRouter
  - ACPPolicyProvider: Feeds policies into policy.PolicyBroker
  - ACPPipelineLoader: Builds PipelineRuntime from DB definitions
  - ACPConfigService:  Unified facade for all ACP reads

Non-negotiable rule: NO hardcoded models, temps, prompts, or
policies in application code. Everything comes from DB rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from engine.db.cache import (
    ConfigCache, NoOpCache,
    agent_config_key, agent_configs_list_key,
    model_route_key, model_routes_list_key,
    tool_policy_key, tool_policies_list_key,
    pipeline_def_key, strategy_weights_key,
)
from engine.db.repositories import AgentConfigRepo, AuditLogRepo
from engine.db.acp_repositories import (
    ModelRouteRepo, ToolPolicyRepo, PipelineDefRepo, StrategyWeightsRepo,
)
from engine.observability import LLMConfig

logger = logging.getLogger("engine.acp")


# ═══════════════════════════════════════════════════════════════
# ACP LLM ROUTER — DB-backed replacement for LLMRouter
# ═══════════════════════════════════════════════════════════════

class ACPLLMRouter:
    """Tiered LLM routing driven by DB agent_configs + model_routes.

    Drop-in replacement for engine.observability.LLMRouter.
    Reads config from Postgres (cached in Redis, TTL 30s).

    Usage:
        router = ACPLLMRouter(session, workspace_id, cache)
        config = router.get("egm_analyzer")  # Returns LLMConfig
    """

    def __init__(
        self, session, workspace_id: str,
        cache: Optional[ConfigCache] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._cache = cache or NoOpCache()
        self._agent_repo = AgentConfigRepo(session)
        self._route_repo = ModelRouteRepo(session)

    def get(self, agent_name: str) -> LLMConfig:
        """Get LLM config for an agent. Reads from DB via cache."""
        config = self._cache.get_or_load(
            key=agent_config_key(self._workspace_id, agent_name),
            loader=lambda: self._agent_repo.get(self._workspace_id, agent_name),
        )

        if not config:
            logger.warning(
                f"No agent config for '{agent_name}' in workspace "
                f"'{self._workspace_id}', using defaults"
            )
            return LLMConfig(model="openai/gpt-4o")

        # Resolve model from route if tier-based
        model = self._resolve_model(config)

        return LLMConfig(
            model=model,
            tier=config.get("model_provider", "heavy"),
            temperature=config.get("temperature", 0.5),
            max_tokens=config.get("max_tokens", 128_000),
            timeout_s=config.get("timeout_sec", 300),
        )

    def register(
        self, agent_name: str, tier: str = "heavy",
        temperature: float = 0.5, max_tokens: int = 128_000,
        **kwargs,
    ) -> LLMConfig:
        """Compatibility shim — writes config to DB then returns LLMConfig.

        Allows existing code that calls router.register() to work
        against the DB-backed router. New code should use the API.
        """
        config_dict = {
            "model_provider": tier,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        self._agent_repo.upsert(
            self._workspace_id, agent_name, config_dict,
            changed_by="system",
            change_reason="Registered via ACPLLMRouter.register()",
        )
        return self.get(agent_name)

    def all_configs(self) -> Dict[str, LLMConfig]:
        """Get all agent configs for this workspace."""
        configs = self._cache.get_or_load_list(
            key=agent_configs_list_key(self._workspace_id),
            loader=lambda: self._agent_repo.list_by_workspace(self._workspace_id),
        )
        return {
            c["agent_name"]: LLMConfig(
                model=self._resolve_model(c),
                tier=c.get("model_provider", "heavy"),
                temperature=c.get("temperature", 0.5),
                max_tokens=c.get("max_tokens", 128_000),
                timeout_s=c.get("timeout_sec", 300),
            )
            for c in configs
            if c.get("enabled", True)
        }

    def _resolve_model(self, agent_config: Dict) -> str:
        """Resolve the actual model string from agent config + model routes.

        If agent_config has explicit model_name, use it.
        Otherwise, look up the model_route for its tier.
        """
        model_name = agent_config.get("model_name", "")
        if model_name and "/" in model_name:
            return model_name  # Already a full model string

        provider = agent_config.get("model_provider", "openai")
        if model_name:
            return f"{provider}/{model_name}"

        # Fall back to model route for the tier
        tier = provider  # In our system, provider doubles as tier
        route = self._cache.get_or_load(
            key=model_route_key(self._workspace_id, tier),
            loader=lambda: self._route_repo.get(self._workspace_id, tier),
        )

        if route and route.get("enabled", True):
            return f"{route['primary_provider']}/{route['primary_model']}"

        return "openai/gpt-4o"  # Ultimate fallback


# ═══════════════════════════════════════════════════════════════
# ACP POLICY PROVIDER — loads ToolPolicy objects from DB
# ═══════════════════════════════════════════════════════════════

class ACPPolicyProvider:
    """Provides tool policies from DB for PolicyBroker consumption.

    Translates DB tool_policy rows into engine.policy.ToolPolicy
    dataclass instances that the existing PolicyBroker understands.

    Usage:
        provider = ACPPolicyProvider(session, workspace_id, cache)
        policies = provider.load_policies()
        broker = PolicyBroker(policies=policies, ...)

        # Or load for a specific agent
        policies = provider.load_policies(agent_name="egm_analyzer")
    """

    def __init__(
        self, session, workspace_id: str,
        cache: Optional[ConfigCache] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._cache = cache or NoOpCache()
        self._repo = ToolPolicyRepo(session)

    def load_policies(self, agent_name: Optional[str] = None) -> list:
        """Load ToolPolicy objects from DB.

        Returns list compatible with PolicyBroker.__init__(policies=...).
        Import is deferred to avoid circular imports.
        """
        from engine.policy import ToolPolicy, ActionScope, ApprovalRequirement

        rows = self._cache.get_or_load_list(
            key=tool_policies_list_key(self._workspace_id),
            loader=lambda: self._repo.list_by_workspace(
                self._workspace_id, agent_name=agent_name
            ),
        )

        policies = []
        for row in rows:
            if not row.get("enabled", True):
                continue

            # Map DB action_scope to engine ActionScope
            scope = row.get("action_scope", "read")

            # Map requires_approval to engine ApprovalRequirement
            approval = (
                ApprovalRequirement.HUMAN.value
                if row.get("requires_approval", False)
                else ApprovalRequirement.AUTO.value
            )

            policy = ToolPolicy(
                tool_name=row["tool_name"],
                description=f"DB policy #{row['id']}",
                allowed_scopes=[scope],
                allowed_domains=row.get("egress_allowed_domains", []),
                allow_egress=bool(row.get("egress_allowed_domains")),
                approval=approval,
                max_calls_per_stage=row.get("rate_limit_per_run", 100),
                max_calls_per_pipeline=row.get("rate_limit_per_run", 100) * 5,
            )
            policies.append(policy)

        return policies

    def get_policy_for_tool(
        self, tool_name: str, agent_name: str = "*",
    ) -> Optional[dict]:
        """Get the resolved policy for a specific tool + agent."""
        return self._cache.get_or_load(
            key=tool_policy_key(self._workspace_id, tool_name, agent_name),
            loader=lambda: self._repo.get_for_tool(
                self._workspace_id, tool_name, agent_name
            ),
        )


# ═══════════════════════════════════════════════════════════════
# ACP PIPELINE LOADER — builds PipelineRuntime from DB defs
# ═══════════════════════════════════════════════════════════════

class ACPPipelineLoader:
    """Loads pipeline definitions from DB and creates StageDef lists.

    Translates DB pipeline_def JSONB stages into engine.runtime.StageDef
    objects that PipelineRuntime can execute.

    Usage:
        loader = ACPPipelineLoader(session, workspace_id, cache)
        stages = loader.load_stages("egm_analysis")
        runtime = PipelineRuntime(stages=stages, handlers=handlers, ...)
    """

    def __init__(
        self, session, workspace_id: str,
        cache: Optional[ConfigCache] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._cache = cache or NoOpCache()
        self._repo = PipelineDefRepo(session)

    def load_stages(self, pipeline_name: str) -> list:
        """Load a pipeline definition and return list of StageDef."""
        from engine.runtime import StageDef, RetryPolicy

        pipeline = self._cache.get_or_load(
            key=pipeline_def_key(self._workspace_id, pipeline_name),
            loader=lambda: self._repo.get(self._workspace_id, pipeline_name),
        )

        if not pipeline:
            raise ValueError(
                f"Pipeline '{pipeline_name}' not found in "
                f"workspace '{self._workspace_id}'"
            )

        if not pipeline.get("enabled", True):
            raise ValueError(
                f"Pipeline '{pipeline_name}' is disabled"
            )

        stages = []
        for s in pipeline.get("stages", []):
            retry_cfg = s.get("retry", {})
            stage = StageDef(
                name=s["name"],
                handler=s.get("handler", s["name"]),
                depends_on=s.get("depends_on", []),
                priority=s.get("priority", 0),
                estimated_seconds=s.get("estimated_seconds", 600),
                timeout_seconds=s.get("timeout_seconds", 1200),
                retry=RetryPolicy(
                    max_retries=retry_cfg.get("max_retries", 2),
                    backoff_factor=retry_cfg.get("backoff_factor", 2.0),
                    base_delay_s=retry_cfg.get("base_delay_s", 5.0),
                    max_delay_s=retry_cfg.get("max_delay_s", 120.0),
                    retryable_errors=retry_cfg.get("retryable_errors", [
                        "context_length_exceeded", "rate_limit", "timeout",
                        "connection", "502", "503", "529",
                    ]),
                ),
                skip_if=s.get("skip_if"),
                run_if=s.get("run_if"),
                description=s.get("description", ""),
                tags=s.get("tags", []),
            )
            stages.append(stage)

        return stages

    def list_pipelines(self) -> list:
        """List all pipeline definitions for this workspace."""
        return self._cache.get_or_load_list(
            key=f"pipeline_defs:{self._workspace_id}:_all",
            loader=lambda: self._repo.list_by_workspace(self._workspace_id),
        )


# ═══════════════════════════════════════════════════════════════
# ACP STRATEGY RESOLVER
# ═══════════════════════════════════════════════════════════════

class ACPStrategyResolver:
    """Reads strategy weights from DB for deal scoring.

    Weights determine how the four investment modes influence
    deal recommendations:
      mode_a: Capital filter (conservative, cash-flow focused)
      mode_b: Vertical integration (own the full stack)
      mode_c: Regional empire (geographic density)
      mode_d: Opportunistic (deal-by-deal IRR maximization)
    """

    def __init__(
        self, session, workspace_id: str,
        cache: Optional[ConfigCache] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._cache = cache or NoOpCache()
        self._repo = StrategyWeightsRepo(session)

    def get_weights(self) -> Dict[str, float]:
        """Get current strategy weights. Returns default if none set."""
        weights = self._cache.get_or_load(
            key=strategy_weights_key(self._workspace_id),
            loader=lambda: self._repo.get_current(self._workspace_id),
        )

        if not weights:
            return {
                "mode_a_capital_filter": 0.25,
                "mode_b_vertical_integration": 0.25,
                "mode_c_regional_empire": 0.25,
                "mode_d_opportunistic": 0.25,
            }

        return {
            "mode_a_capital_filter": weights["mode_a_capital_filter"],
            "mode_b_vertical_integration": weights["mode_b_vertical_integration"],
            "mode_c_regional_empire": weights["mode_c_regional_empire"],
            "mode_d_opportunistic": weights["mode_d_opportunistic"],
        }

    def score_deal(
        self, deal_scores: Dict[str, float],
    ) -> float:
        """Apply strategy weights to a deal's per-mode scores.

        deal_scores: {mode_a: 0.8, mode_b: 0.3, ...}
        Returns: weighted composite score
        """
        weights = self.get_weights()
        total = 0.0
        for mode, weight in weights.items():
            score_key = mode  # Same key names
            total += weight * deal_scores.get(score_key, 0.0)
        return total


# ═══════════════════════════════════════════════════════════════
# UNIFIED CONFIG SERVICE
# ═══════════════════════════════════════════════════════════════

class ACPConfigService:
    """Unified facade for all ACP configuration.

    Provides a single entry point for pipeline runners to
    get everything they need from the control plane.

    Usage:
        acp = ACPConfigService(session, workspace_id, cache)
        llm_config = acp.router.get("egm_analyzer")
        policies = acp.policies.load_policies()
        stages = acp.pipelines.load_stages("egm_analysis")
        weights = acp.strategy.get_weights()
    """

    def __init__(
        self, session, workspace_id: str,
        cache: Optional[ConfigCache] = None,
    ):
        self.workspace_id = workspace_id
        self.router = ACPLLMRouter(session, workspace_id, cache)
        self.policies = ACPPolicyProvider(session, workspace_id, cache)
        self.pipelines = ACPPipelineLoader(session, workspace_id, cache)
        self.strategy = ACPStrategyResolver(session, workspace_id, cache)

    def get_agent_tools(self, agent_name: str) -> List[str]:
        """Get the allowed tools for an agent from its config."""
        config = self.router._cache.get_or_load(
            key=agent_config_key(self.workspace_id, agent_name),
            loader=lambda: self.router._agent_repo.get(
                self.workspace_id, agent_name
            ),
        )
        if config:
            return config.get("tool_allowlist", [])
        return []
