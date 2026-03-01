"""
tests/test_phase1_acp.py — Agent Control Plane Tests
======================================================
Tests for Phase 1: ACP repositories, cache layer, DB-backed adapters,
and the full config-to-runtime pipeline.

Run: pytest tests/test_phase1_acp.py -v
"""

import os
import pytest

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.db.models import Base
from engine.db.repositories import AgentConfigRepo, AuditLogRepo
from engine.db.acp_repositories import (
    ModelRouteRepo, ToolPolicyRepo, PipelineDefRepo, StrategyWeightsRepo,
)
from engine.db.cache import ConfigCache, NoOpCache, agent_config_key
from engine.tenants import Organization, Workspace, UserIdentity
from engine.db.repositories import OrganizationRepo, WorkspaceRepo, UserRepo


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def engine():
    eng = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    sess = Session()
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def seed(session):
    """Seed org + workspace for all tests."""
    OrganizationRepo(session).create(
        Organization(org_id="org1", name="Test Org")
    )
    WorkspaceRepo(session).create(
        Workspace(workspace_id="ws1", org_id="org1", name="Main")
    )
    session.flush()


# ═══════════════════════════════════════════════════════════════
# MODEL ROUTE REPO
# ═══════════════════════════════════════════════════════════════

class TestModelRouteRepo:
    def test_create_and_get(self, session, seed):
        repo = ModelRouteRepo(session)
        result = repo.upsert("ws1", "premium", {
            "primary_provider": "anthropic",
            "primary_model": "claude-sonnet-4-20250514",
            "fallback_provider": "openai",
            "fallback_model": "gpt-4o",
            "cost_cap_per_run": 5.0,
        })
        session.flush()

        assert result["tier"] == "premium"
        assert result["primary_model"] == "claude-sonnet-4-20250514"

        fetched = repo.get("ws1", "premium")
        assert fetched["fallback_model"] == "gpt-4o"
        assert fetched["cost_cap_per_run"] == 5.0

    def test_upsert_updates(self, session, seed):
        repo = ModelRouteRepo(session)
        repo.upsert("ws1", "heavy", {
            "primary_provider": "openai",
            "primary_model": "gpt-4o",
        })
        session.flush()

        updated = repo.upsert("ws1", "heavy", {
            "primary_model": "gpt-4o-mini",
            "cost_cap_per_run": 2.0,
        })
        session.flush()

        assert updated["primary_model"] == "gpt-4o-mini"
        assert updated["cost_cap_per_run"] == 2.0

    def test_list_by_workspace(self, session, seed):
        repo = ModelRouteRepo(session)
        repo.upsert("ws1", "premium", {
            "primary_provider": "anthropic", "primary_model": "claude-sonnet-4-20250514"
        })
        repo.upsert("ws1", "heavy", {
            "primary_provider": "openai", "primary_model": "gpt-4o"
        })
        repo.upsert("ws1", "light", {
            "primary_provider": "openai", "primary_model": "gpt-4o-mini"
        })
        session.flush()

        routes = repo.list_by_workspace("ws1")
        tiers = {r["tier"] for r in routes}
        assert tiers == {"premium", "heavy", "light"}

    def test_delete(self, session, seed):
        repo = ModelRouteRepo(session)
        repo.upsert("ws1", "delete_me", {
            "primary_provider": "test", "primary_model": "test"
        })
        session.flush()

        assert repo.delete("ws1", "delete_me") is True
        assert repo.get("ws1", "delete_me") is None
        assert repo.delete("ws1", "nonexistent") is False


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY REPO
# ═══════════════════════════════════════════════════════════════

class TestToolPolicyRepo:
    def test_create_and_get(self, session, seed):
        repo = ToolPolicyRepo(session)
        result = repo.create("ws1", {
            "tool_name": "irr_calculator",
            "agent_name": "*",
            "action_scope": "compute",
            "rate_limit_per_min": 30,
            "rate_limit_per_run": 50,
        })
        session.flush()

        assert result["tool_name"] == "irr_calculator"
        assert result["rate_limit_per_min"] == 30

        fetched = repo.get(result["id"])
        assert fetched is not None
        assert fetched["action_scope"] == "compute"

    def test_agent_specific_policy(self, session, seed):
        repo = ToolPolicyRepo(session)
        # Wildcard policy
        repo.create("ws1", {
            "tool_name": "web_search",
            "agent_name": "*",
            "rate_limit_per_min": 10,
        })
        # Agent-specific policy (more permissive)
        repo.create("ws1", {
            "tool_name": "web_search",
            "agent_name": "market_analyzer",
            "rate_limit_per_min": 60,
        })
        session.flush()

        # Should get agent-specific policy
        specific = repo.get_for_tool("ws1", "web_search", "market_analyzer")
        assert specific is not None
        assert specific["agent_name"] == "market_analyzer"
        assert specific["rate_limit_per_min"] == 60

        # Fallback to wildcard for unknown agent
        wildcard = repo.get_for_tool("ws1", "web_search", "unknown_agent")
        assert wildcard is not None
        assert wildcard["agent_name"] == "*"
        assert wildcard["rate_limit_per_min"] == 10

    def test_list_filters_by_agent(self, session, seed):
        repo = ToolPolicyRepo(session)
        repo.create("ws1", {"tool_name": "tool_a", "agent_name": "*"})
        repo.create("ws1", {"tool_name": "tool_b", "agent_name": "agent_x"})
        repo.create("ws1", {"tool_name": "tool_c", "agent_name": "agent_x"})
        session.flush()

        # Filter for agent_x should get its specific policies + wildcard
        policies = repo.list_by_workspace("ws1", agent_name="agent_x")
        names = {p["tool_name"] for p in policies}
        assert "tool_a" in names  # wildcard applies
        assert "tool_b" in names
        assert "tool_c" in names

    def test_update(self, session, seed):
        repo = ToolPolicyRepo(session)
        created = repo.create("ws1", {
            "tool_name": "update_test",
            "requires_approval": False,
        })
        session.flush()

        updated = repo.update(created["id"], {"requires_approval": True})
        assert updated["requires_approval"] is True

    def test_delete(self, session, seed):
        repo = ToolPolicyRepo(session)
        created = repo.create("ws1", {"tool_name": "delete_test"})
        session.flush()

        assert repo.delete(created["id"]) is True
        assert repo.get(created["id"]) is None


# ═══════════════════════════════════════════════════════════════
# PIPELINE DEF REPO
# ═══════════════════════════════════════════════════════════════

class TestPipelineDefRepo:
    def test_create_and_get(self, session, seed):
        repo = PipelineDefRepo(session)
        stages = [
            {"name": "intake", "handler": "intake_handler", "depends_on": []},
            {"name": "analyze", "handler": "analyze_handler", "depends_on": ["intake"]},
            {"name": "score", "handler": "score_handler", "depends_on": ["analyze"]},
        ]
        result = repo.upsert("ws1", "egm_analysis", stages)
        session.flush()

        assert result["name"] == "egm_analysis"
        assert result["version"] == 1
        assert len(result["stages"]) == 3

        fetched = repo.get("ws1", "egm_analysis")
        assert fetched["stages"][1]["depends_on"] == ["intake"]

    def test_upsert_increments_version(self, session, seed):
        repo = PipelineDefRepo(session)
        repo.upsert("ws1", "versioned_pipe", [
            {"name": "step1", "handler": "h1"},
        ])
        session.flush()

        v2 = repo.upsert("ws1", "versioned_pipe", [
            {"name": "step1", "handler": "h1"},
            {"name": "step2", "handler": "h2", "depends_on": ["step1"]},
        ])
        session.flush()

        assert v2["version"] == 2
        assert len(v2["stages"]) == 2

    def test_list_by_workspace(self, session, seed):
        repo = PipelineDefRepo(session)
        repo.upsert("ws1", "pipe_a", [{"name": "s1", "handler": "h1"}])
        repo.upsert("ws1", "pipe_b", [{"name": "s1", "handler": "h1"}])
        session.flush()

        pipes = repo.list_by_workspace("ws1")
        names = {p["name"] for p in pipes}
        assert "pipe_a" in names
        assert "pipe_b" in names

    def test_delete(self, session, seed):
        repo = PipelineDefRepo(session)
        repo.upsert("ws1", "delete_pipe", [{"name": "s1", "handler": "h1"}])
        session.flush()

        assert repo.delete("ws1", "delete_pipe") is True
        assert repo.get("ws1", "delete_pipe") is None


# ═══════════════════════════════════════════════════════════════
# STRATEGY WEIGHTS REPO
# ═══════════════════════════════════════════════════════════════

class TestStrategyWeightsRepo:
    def test_set_and_get_current(self, session, seed):
        repo = StrategyWeightsRepo(session)
        result = repo.set_weights("ws1", {
            "mode_a_capital_filter": 0.4,
            "mode_b_vertical_integration": 0.3,
            "mode_c_regional_empire": 0.2,
            "mode_d_opportunistic": 0.1,
        }, created_by="u1")
        session.flush()

        assert result["version"] == 1
        assert result["mode_a_capital_filter"] == 0.4

        current = repo.get_current("ws1")
        assert current["version"] == 1
        assert current["mode_d_opportunistic"] == 0.1

    def test_versioning(self, session, seed):
        repo = StrategyWeightsRepo(session)
        repo.set_weights("ws1", {
            "mode_a_capital_filter": 0.25,
            "mode_b_vertical_integration": 0.25,
            "mode_c_regional_empire": 0.25,
            "mode_d_opportunistic": 0.25,
        })
        session.flush()

        v2 = repo.set_weights("ws1", {
            "mode_a_capital_filter": 0.6,
            "mode_b_vertical_integration": 0.2,
            "mode_c_regional_empire": 0.1,
            "mode_d_opportunistic": 0.1,
        })
        session.flush()

        assert v2["version"] == 2

        # Current should be v2
        current = repo.get_current("ws1")
        assert current["version"] == 2
        assert current["mode_a_capital_filter"] == 0.6

        # Can still get v1
        v1 = repo.get_version("ws1", 1)
        assert v1["mode_a_capital_filter"] == 0.25

    def test_list_versions(self, session, seed):
        repo = StrategyWeightsRepo(session)
        for i in range(3):
            repo.set_weights("ws1", {
                "mode_a_capital_filter": 0.25,
                "mode_b_vertical_integration": 0.25,
                "mode_c_regional_empire": 0.25,
                "mode_d_opportunistic": 0.25,
            })
        session.flush()

        versions = repo.list_versions("ws1")
        assert len(versions) >= 3
        # Most recent first
        assert versions[0]["version"] > versions[-1]["version"]

    def test_no_weights_returns_none(self, session, seed):
        repo = StrategyWeightsRepo(session)
        assert repo.get_current("ws_empty") is None


# ═══════════════════════════════════════════════════════════════
# CACHE TESTS (NoOpCache — no Redis required)
# ═══════════════════════════════════════════════════════════════

class TestNoOpCache:
    def test_always_calls_loader(self):
        cache = NoOpCache()
        call_count = 0

        def loader():
            nonlocal call_count
            call_count += 1
            return {"data": "fresh"}

        # Every call should hit the loader (no caching)
        cache.get_or_load("key1", loader)
        cache.get_or_load("key1", loader)
        cache.get_or_load("key1", loader)
        assert call_count == 3

    def test_returns_loader_result(self):
        cache = NoOpCache()
        result = cache.get_or_load("key", lambda: {"value": 42})
        assert result == {"value": 42}

    def test_list_loader(self):
        cache = NoOpCache()
        result = cache.get_or_load_list("key", lambda: [1, 2, 3])
        assert result == [1, 2, 3]


class TestConfigCacheOffline:
    """Test ConfigCache behavior when Redis is not available."""

    def test_unavailable_redis_falls_back(self):
        cache = ConfigCache(redis_url="redis://nonexistent:9999/0")
        assert cache.is_available is False

        # Should still work by calling loader directly
        result = cache.get_or_load("key", lambda: {"fallback": True})
        assert result == {"fallback": True}

    def test_invalidate_noop_when_unavailable(self):
        cache = ConfigCache()  # No URL = not available
        cache.invalidate("key")  # Should not raise
        cache.invalidate_pattern("prefix:*")
        cache.invalidate_workspace("ws1")


# ═══════════════════════════════════════════════════════════════
# ACP ADAPTER TESTS
# ═══════════════════════════════════════════════════════════════

class TestACPLLMRouter:
    def test_get_agent_config(self, session, seed):
        from engine.acp import ACPLLMRouter

        # Create agent config
        AgentConfigRepo(session).upsert("ws1", "test_agent", {
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-4-20250514",
            "temperature": 0.3,
            "max_tokens": 64000,
            "timeout_sec": 120,
        }, changed_by="test")
        session.flush()

        router = ACPLLMRouter(session, "ws1", NoOpCache())
        config = router.get("test_agent")

        assert config.temperature == 0.3
        assert config.max_tokens == 64000
        assert config.timeout_s == 120
        assert "claude" in config.model.lower() or "anthropic" in config.model.lower()

    def test_default_when_not_found(self, session, seed):
        from engine.acp import ACPLLMRouter

        router = ACPLLMRouter(session, "ws1", NoOpCache())
        config = router.get("nonexistent_agent")

        # Should return a sensible default
        assert config.model is not None
        assert config.temperature == 0.5  # default

    def test_all_configs(self, session, seed):
        from engine.acp import ACPLLMRouter

        AgentConfigRepo(session).upsert("ws1", "agent_a", {
            "model_name": "gpt-4o", "temperature": 0.2
        }, changed_by="test")
        AgentConfigRepo(session).upsert("ws1", "agent_b", {
            "model_name": "claude-sonnet-4-20250514", "temperature": 0.8
        }, changed_by="test")
        session.flush()

        router = ACPLLMRouter(session, "ws1", NoOpCache())
        all_cfgs = router.all_configs()

        assert "agent_a" in all_cfgs
        assert "agent_b" in all_cfgs
        assert all_cfgs["agent_a"].temperature == 0.2
        assert all_cfgs["agent_b"].temperature == 0.8

    def test_register_compatibility_shim(self, session, seed):
        from engine.acp import ACPLLMRouter

        router = ACPLLMRouter(session, "ws1", NoOpCache())
        config = router.register("new_agent", tier="light", temperature=0.1)
        session.flush()

        assert config.temperature == 0.1
        # Should be persisted in DB
        db_config = AgentConfigRepo(session).get("ws1", "new_agent")
        assert db_config is not None
        assert db_config["temperature"] == 0.1


class TestACPPolicyProvider:
    def test_load_policies(self, session, seed):
        from engine.acp import ACPPolicyProvider

        ToolPolicyRepo(session).create("ws1", {
            "tool_name": "irr_calculator",
            "agent_name": "*",
            "action_scope": "compute",
            "rate_limit_per_run": 50,
            "requires_approval": False,
        })
        ToolPolicyRepo(session).create("ws1", {
            "tool_name": "contract_signer",
            "agent_name": "*",
            "action_scope": "write",
            "requires_approval": True,
        })
        session.flush()

        provider = ACPPolicyProvider(session, "ws1", NoOpCache())
        policies = provider.load_policies()

        assert len(policies) == 2
        names = {p.tool_name for p in policies}
        assert "irr_calculator" in names
        assert "contract_signer" in names

        # Check approval mapping
        signer = [p for p in policies if p.tool_name == "contract_signer"][0]
        assert signer.approval == "human"

    def test_disabled_policies_excluded(self, session, seed):
        from engine.acp import ACPPolicyProvider

        ToolPolicyRepo(session).create("ws1", {
            "tool_name": "enabled_tool", "enabled": True,
        })
        ToolPolicyRepo(session).create("ws1", {
            "tool_name": "disabled_tool", "enabled": False,
        })
        session.flush()

        provider = ACPPolicyProvider(session, "ws1", NoOpCache())
        policies = provider.load_policies()
        names = {p.tool_name for p in policies}
        assert "enabled_tool" in names
        assert "disabled_tool" not in names


class TestACPPipelineLoader:
    def test_load_stages(self, session, seed):
        from engine.acp import ACPPipelineLoader

        PipelineDefRepo(session).upsert("ws1", "egm_analysis", [
            {
                "name": "intake",
                "handler": "intake_handler",
                "depends_on": [],
                "priority": 0,
                "timeout_seconds": 60,
            },
            {
                "name": "forecast",
                "handler": "forecast_handler",
                "depends_on": ["intake"],
                "priority": 0,
                "estimated_seconds": 120,
                "retry": {"max_retries": 5, "backoff_factor": 3.0},
            },
            {
                "name": "score",
                "handler": "score_handler",
                "depends_on": ["forecast"],
                "priority": 0,
            },
        ])
        session.flush()

        loader = ACPPipelineLoader(session, "ws1", NoOpCache())
        stages = loader.load_stages("egm_analysis")

        assert len(stages) == 3
        assert stages[0].name == "intake"
        assert stages[1].depends_on == ["intake"]
        assert stages[1].retry.max_retries == 5
        assert stages[1].retry.backoff_factor == 3.0

    def test_missing_pipeline_raises(self, session, seed):
        from engine.acp import ACPPipelineLoader

        loader = ACPPipelineLoader(session, "ws1", NoOpCache())
        with pytest.raises(ValueError, match="not found"):
            loader.load_stages("nonexistent")

    def test_disabled_pipeline_raises(self, session, seed):
        from engine.acp import ACPPipelineLoader

        PipelineDefRepo(session).upsert("ws1", "disabled_pipe", [
            {"name": "s1", "handler": "h1"},
        ])
        session.flush()

        # Disable it (raw update)
        from engine.db.models import PipelineDefRow
        from sqlalchemy import update
        session.execute(
            update(PipelineDefRow)
            .where(PipelineDefRow.name == "disabled_pipe")
            .values(enabled=False)
        )
        session.flush()

        loader = ACPPipelineLoader(session, "ws1", NoOpCache())
        with pytest.raises(ValueError, match="disabled"):
            loader.load_stages("disabled_pipe")


class TestACPStrategyResolver:
    def test_get_weights(self, session, seed):
        from engine.acp import ACPStrategyResolver

        StrategyWeightsRepo(session).set_weights("ws1", {
            "mode_a_capital_filter": 0.5,
            "mode_b_vertical_integration": 0.3,
            "mode_c_regional_empire": 0.1,
            "mode_d_opportunistic": 0.1,
        })
        session.flush()

        resolver = ACPStrategyResolver(session, "ws1", NoOpCache())
        weights = resolver.get_weights()

        assert weights["mode_a_capital_filter"] == 0.5
        assert weights["mode_d_opportunistic"] == 0.1

    def test_default_weights_when_none_set(self, session, seed):
        from engine.acp import ACPStrategyResolver

        resolver = ACPStrategyResolver(session, "ws_empty", NoOpCache())
        weights = resolver.get_weights()

        assert weights["mode_a_capital_filter"] == 0.25
        assert sum(weights.values()) == 1.0

    def test_score_deal(self, session, seed):
        from engine.acp import ACPStrategyResolver

        StrategyWeightsRepo(session).set_weights("ws1", {
            "mode_a_capital_filter": 0.5,
            "mode_b_vertical_integration": 0.2,
            "mode_c_regional_empire": 0.2,
            "mode_d_opportunistic": 0.1,
        })
        session.flush()

        resolver = ACPStrategyResolver(session, "ws1", NoOpCache())
        score = resolver.score_deal({
            "mode_a_capital_filter": 0.9,      # Strong capital filter score
            "mode_b_vertical_integration": 0.4,
            "mode_c_regional_empire": 0.6,
            "mode_d_opportunistic": 0.8,
        })

        # 0.5*0.9 + 0.2*0.4 + 0.2*0.6 + 0.1*0.8 = 0.45 + 0.08 + 0.12 + 0.08 = 0.73
        assert abs(score - 0.73) < 0.001


class TestACPConfigService:
    def test_unified_facade(self, session, seed):
        from engine.acp import ACPConfigService

        # Setup data
        AgentConfigRepo(session).upsert("ws1", "analyzer", {
            "model_name": "gpt-4o", "temperature": 0.3,
            "tool_allowlist": ["irr_calculator", "sensitivity"],
        }, changed_by="test")
        ToolPolicyRepo(session).create("ws1", {
            "tool_name": "irr_calculator", "action_scope": "compute",
        })
        PipelineDefRepo(session).upsert("ws1", "test_pipe", [
            {"name": "s1", "handler": "h1"},
        ])
        StrategyWeightsRepo(session).set_weights("ws1", {
            "mode_a_capital_filter": 0.4,
            "mode_b_vertical_integration": 0.2,
            "mode_c_regional_empire": 0.2,
            "mode_d_opportunistic": 0.2,
        })
        session.flush()

        acp = ACPConfigService(session, "ws1", NoOpCache())

        # Router
        llm = acp.router.get("analyzer")
        assert llm.temperature == 0.3

        # Policies
        policies = acp.policies.load_policies()
        assert len(policies) >= 1

        # Pipeline
        stages = acp.pipelines.load_stages("test_pipe")
        assert len(stages) == 1

        # Strategy
        weights = acp.strategy.get_weights()
        assert weights["mode_a_capital_filter"] == 0.4

        # Agent tools
        tools = acp.get_agent_tools("analyzer")
        assert "irr_calculator" in tools


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: CONFIG CHANGE → RUNTIME EFFECT
# ═══════════════════════════════════════════════════════════════

class TestConfigToRuntimeIntegration:
    """Verify the non-negotiable rule: config changes in DB take
    effect on next pipeline run without code deploy."""

    def test_temperature_change_takes_effect(self, session, seed):
        from engine.acp import ACPLLMRouter

        repo = AgentConfigRepo(session)
        repo.upsert("ws1", "hot_agent", {
            "temperature": 0.9,
        }, changed_by="test")
        session.flush()

        router = ACPLLMRouter(session, "ws1", NoOpCache())
        assert router.get("hot_agent").temperature == 0.9

        # Change temperature
        repo.upsert("ws1", "hot_agent", {
            "temperature": 0.1,
        }, changed_by="test", change_reason="Cool it down")
        session.flush()

        # Next read should see the new value
        assert router.get("hot_agent").temperature == 0.1

    def test_model_swap_takes_effect(self, session, seed):
        from engine.acp import ACPLLMRouter

        repo = AgentConfigRepo(session)
        repo.upsert("ws1", "swap_agent", {
            "model_provider": "openai",
            "model_name": "gpt-4o",
        }, changed_by="test")
        session.flush()

        router = ACPLLMRouter(session, "ws1", NoOpCache())
        config1 = router.get("swap_agent")
        assert "gpt-4o" in config1.model

        # Swap to different model
        repo.upsert("ws1", "swap_agent", {
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-4-20250514",
        }, changed_by="test", change_reason="Switch to Anthropic")
        session.flush()

        config2 = router.get("swap_agent")
        assert "claude" in config2.model.lower() or "anthropic" in config2.model.lower()

    def test_pipeline_stage_addition_takes_effect(self, session, seed):
        from engine.acp import ACPPipelineLoader

        repo = PipelineDefRepo(session)
        repo.upsert("ws1", "evolving_pipe", [
            {"name": "step1", "handler": "h1"},
        ])
        session.flush()

        loader = ACPPipelineLoader(session, "ws1", NoOpCache())
        stages = loader.load_stages("evolving_pipe")
        assert len(stages) == 1

        # Add a stage
        repo.upsert("ws1", "evolving_pipe", [
            {"name": "step1", "handler": "h1"},
            {"name": "step2", "handler": "h2", "depends_on": ["step1"]},
        ])
        session.flush()

        stages = loader.load_stages("evolving_pipe")
        assert len(stages) == 2
        assert stages[1].depends_on == ["step1"]

    def test_full_audit_trail(self, session, seed):
        """Every config change must produce an audit entry."""
        audit_repo = AuditLogRepo(session)
        agent_repo = AgentConfigRepo(session)

        # Initial create
        agent_repo.upsert("ws1", "audited_agent", {
            "temperature": 0.5,
        }, changed_by="admin")
        audit_repo.append(
            workspace_id="ws1",
            action="agent_config.create",
            resource="agent:audited_agent",
            outcome="success",
            user_id="admin",
            details={"temperature": 0.5},
        )

        # Update
        agent_repo.upsert("ws1", "audited_agent", {
            "temperature": 0.2,
        }, changed_by="admin", change_reason="Reduce randomness")
        audit_repo.append(
            workspace_id="ws1",
            action="agent_config.update",
            resource="agent:audited_agent",
            outcome="success",
            user_id="admin",
            details={"temperature": 0.2, "reason": "Reduce randomness"},
        )
        session.flush()

        # Verify audit trail
        entries = audit_repo.list_entries("ws1", action_filter="agent_config.update")
        assert len(entries) >= 1
        assert entries[0]["details"]["temperature"] == 0.2

        # Verify version history
        history = agent_repo.get_version_history("ws1", "audited_agent")
        assert len(history) >= 1
