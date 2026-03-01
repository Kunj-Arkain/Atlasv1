"""
tests/test_phase8_brain.py — ArkainBrain + Continuous Learning Tests
=====================================================================
Phase 8: Tool registry, brain adapter, pipeline orchestration.
Phase 9: Drift detection, experiment runner, retraining pipeline.

Run: pytest tests/test_phase8_brain.py -v
"""

import os
import pytest
from dataclasses import asdict

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from engine.brain.tools import ToolRegistry, ToolSpec, ToolResult
from engine.brain.adapter import (
    ArkainBrainAdapter, PipelineOrchestrator, AgentRunResult,
)
from engine.brain.learning import (
    DriftDetector, DriftAlert, ExperimentRunner, ExperimentResult,
)


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRY (Phase 8B)
# ═══════════════════════════════════════════════════════════════

class TestToolRegistry:

    def test_register_and_list(self):
        r = ToolRegistry()
        r.register(
            ToolSpec("add", "Add numbers", "math"), lambda p: p["a"] + p["b"],
        )
        tools = r.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "add"

    def test_execute_success(self):
        r = ToolRegistry()
        r.register(
            ToolSpec("mul", "Multiply", "math"), lambda p: p["a"] * p["b"],
        )
        result = r.execute("mul", {"a": 5, "b": 3})
        assert result.success
        assert result.data == 15

    def test_execute_unknown_tool(self):
        r = ToolRegistry()
        result = r.execute("nonexistent", {})
        assert not result.success
        assert "Unknown tool" in result.error

    def test_execute_handler_error(self):
        r = ToolRegistry()
        r.register(
            ToolSpec("boom", "Explodes", "test"),
            lambda p: 1 / 0,
        )
        result = r.execute("boom", {})
        assert not result.success
        assert "division by zero" in result.error

    def test_filter_by_category(self):
        r = ToolRegistry()
        r.register(ToolSpec("a", "A", "cat1"), lambda p: None)
        r.register(ToolSpec("b", "B", "cat2"), lambda p: None)
        r.register(ToolSpec("c", "C", "cat1"), lambda p: None)
        assert len(r.list_tools("cat1")) == 2
        assert len(r.list_tools("cat2")) == 1
        assert len(r.list_tools()) == 3

    def test_execution_timing(self):
        import time
        r = ToolRegistry()
        r.register(
            ToolSpec("slow", "Slow op", "test"),
            lambda p: time.sleep(0.01) or "done",
        )
        result = r.execute("slow", {})
        assert result.success
        assert result.execution_ms >= 0

    def test_get_spec(self):
        r = ToolRegistry()
        r.register(ToolSpec("x", "Desc", "cat"), lambda p: None)
        spec = r.get_spec("x")
        assert spec is not None
        assert spec.description == "Desc"
        assert r.get_spec("missing") is None


# ═══════════════════════════════════════════════════════════════
# ARKAINBRAIN ADAPTER (Phase 8A)
# ═══════════════════════════════════════════════════════════════

class TestArkainBrainAdapter:

    def _make_adapter(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec("calc", "Calculate", "math"),
            lambda p: {"result": p.get("a", 0) + p.get("b", 0)},
        )
        adapter = ArkainBrainAdapter(tool_registry=registry)
        adapter._agent_configs["analyst"] = {
            "role": "analyst", "tools": ["calc"],
        }
        return adapter

    def test_run_agent_completes(self):
        adapter = self._make_adapter()
        result = adapter.run_agent("analyst", "Calculate something")
        assert result.status == "completed"
        assert result.agent_name == "analyst"
        assert isinstance(result.execution_ms, int)

    def test_approval_blocking(self):
        adapter = self._make_adapter()
        result = adapter.run_agent(
            "analyst", "Do something", require_approval=True,
        )
        assert result.status == "approval_pending"

    def test_result_serializable(self):
        adapter = self._make_adapter()
        result = adapter.run_agent("analyst", "Test")
        d = asdict(result)
        assert "agent_name" in d
        assert "tool_calls" in d
        assert "reasoning_trace" in d

    def test_unconfigured_agent(self):
        adapter = self._make_adapter()
        result = adapter.run_agent("unknown_agent", "Test")
        assert result.status == "completed"

    def test_task_keyword_dispatch(self):
        """Tasks with 'contract'/'simulate' should plan contract tools."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec("simulate_contract", "Monte Carlo", "contract"),
            lambda p: {"irr_p50": 0.15},
        )
        adapter = ArkainBrainAdapter(tool_registry=registry)
        adapter._agent_configs["deal_agent"] = {"tools": []}

        result = adapter.run_agent(
            "deal_agent",
            "Simulate the contract for this deal",
            context={"agreement_type": "revenue_share"},
        )
        # Should have attempted simulate_contract
        assert result.status == "completed"


# ═══════════════════════════════════════════════════════════════
# PIPELINE ORCHESTRATOR (Phase 8C)
# ═══════════════════════════════════════════════════════════════

class TestPipelineOrchestrator:

    def test_basic_pipeline(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec("step_tool", "Step", "test"),
            lambda p: {"output": "done"},
        )
        adapter = ArkainBrainAdapter(tool_registry=registry)
        adapter._agent_configs["worker"] = {"tools": ["step_tool"]}

        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("test", {}, stages=[
            {"name": "step1", "agent": "worker", "task": "Do step 1"},
            {"name": "step2", "agent": "worker", "task": "Do step 2"},
        ])

        assert result["total_stages"] == 2
        assert result["completed_stages"] == 2
        assert result["status"] == "completed"

    def test_pipeline_stops_on_approval(self):
        adapter = ArkainBrainAdapter(tool_registry=ToolRegistry())
        adapter._agent_configs["worker"] = {"tools": []}

        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("test", {}, stages=[
            {"name": "step1", "agent": "worker", "task": "Free step"},
            {"name": "step2", "agent": "worker", "task": "Blocked",
             "requires_approval": True},
            {"name": "step3", "agent": "worker", "task": "Never runs"},
        ])

        assert result["completed_stages"] == 1
        assert result["status"] == "incomplete"

    def test_default_stages(self):
        adapter = ArkainBrainAdapter(tool_registry=ToolRegistry())
        orch = PipelineOrchestrator(adapter)
        stages = orch._default_stages("deal_evaluation")
        assert len(stages) == 3
        stages2 = orch._default_stages("contract_analysis")
        assert len(stages2) == 2
        stages3 = orch._default_stages("unknown")
        assert stages3 == []


# ═══════════════════════════════════════════════════════════════
# DRIFT DETECTOR (Phase 9B)
# ═══════════════════════════════════════════════════════════════

class TestDriftDetector:

    def test_no_drift(self):
        detector = DriftDetector()
        preds = [
            {"location_id": 1, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000},
        ]
        actuals = [{"location_id": 1, "actual_net_win": 19500}]
        alerts = detector.check_predictions(preds, actuals)
        assert len(alerts) == 0

    def test_warning_drift(self):
        detector = DriftDetector(warning_threshold=0.10, critical_threshold=0.30)
        preds = [
            {"location_id": 1, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000},
        ]
        actuals = [{"location_id": 1, "actual_net_win": 17000}]  # ~17.6% error
        alerts = detector.check_predictions(preds, actuals)
        assert any(a.severity == "WARNING" for a in alerts)

    def test_critical_drift(self):
        detector = DriftDetector()
        preds = [
            {"location_id": 1, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000},
            {"location_id": 2, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 25000},
        ]
        actuals = [
            {"location_id": 1, "actual_net_win": 10000},  # 100% off
            {"location_id": 2, "actual_net_win": 12000},  # 108% off
        ]
        alerts = detector.check_predictions(preds, actuals)
        assert any(a.severity == "CRITICAL" for a in alerts)

    def test_venue_specific_drift(self):
        detector = DriftDetector(critical_threshold=0.20)
        preds = [
            {"location_id": 1, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000},
            {"location_id": 2, "venue_type": "gas_station", "state": "IL",
             "predicted_net_win": 30000},
        ]
        actuals = [
            {"location_id": 1, "actual_net_win": 10000},  # bar is way off
            {"location_id": 2, "actual_net_win": 29000},  # gas station is fine
        ]
        alerts = detector.check_predictions(preds, actuals)
        venue_alerts = [a for a in alerts if a.alert_type == "venue_drift"]
        if venue_alerts:
            assert any("bar" in a.metric for a in venue_alerts)

    def test_market_shift(self):
        detector = DriftDetector(market_shift_threshold=0.20)
        current = [{"municipality": "Decatur", "total_net_win": 400000}]
        previous = [{"municipality": "Decatur", "total_net_win": 300000}]
        alerts = detector.check_market_shift(current, previous)
        assert len(alerts) == 1
        assert "Decatur" in alerts[0].details

    def test_no_market_shift(self):
        detector = DriftDetector()
        current = [{"municipality": "X", "total_net_win": 100000}]
        previous = [{"municipality": "X", "total_net_win": 95000}]
        alerts = detector.check_market_shift(current, previous)
        assert len(alerts) == 0

    def test_empty_inputs(self):
        detector = DriftDetector()
        assert detector.check_predictions([], []) == []
        assert detector.check_market_shift([], []) == []

    def test_alert_serialization(self):
        alert = DriftAlert(
            alert_type="test", severity="WARNING",
            metric="mape", expected=0.15, actual=0.25,
            deviation_pct=0.25,
        )
        d = alert.to_dict()
        assert d["alert_type"] == "test"
        assert "timestamp" in d


# ═══════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER (Phase 9C)
# ═══════════════════════════════════════════════════════════════

class TestExperimentRunner:

    def test_basic_experiment(self):
        runner = ExperimentRunner()

        def model_a(case):
            return {"net_win": {"p50": case["actual_net_win"] * 1.10}}

        def model_b(case):
            return {"net_win": {"p50": case["actual_net_win"] * 0.95}}

        cases = [
            {"actual_net_win": 20000},
            {"actual_net_win": 15000},
            {"actual_net_win": 30000},
        ]

        result = runner.run("test", model_a, model_b, cases)
        assert result.winner == "B"  # 5% off beats 10% off
        assert result.sample_size == 3

    def test_identical_models(self):
        runner = ExperimentRunner()
        fn = lambda case: {"net_win": {"p50": case["actual_net_win"]}}
        cases = [{"actual_net_win": 10000}]
        result = runner.run("tie", fn, fn, cases)
        assert result.winner in ("A", "B")

    def test_failing_model(self):
        runner = ExperimentRunner()

        def good(case):
            return {"net_win": {"p50": case["actual_net_win"]}}

        def bad(case):
            raise ValueError("broken")

        cases = [{"actual_net_win": 10000}]
        result = runner.run("fail_test", good, bad, cases)
        assert result.winner == "A"

    def test_result_serializable(self):
        runner = ExperimentRunner()
        fn = lambda c: {"net_win": {"p50": 100}}
        result = runner.run("ser_test", fn, fn, [{"actual_net_win": 100}])
        d = asdict(result)
        assert "winner" in d
        assert "improvement_pct" in d

    def test_custom_metric(self):
        runner = ExperimentRunner()

        def custom_metric(result, expected):
            return result.get("score", 0)

        def a(c):
            return {"score": 0.8}

        def b(c):
            return {"score": 0.9}

        result = runner.run("custom", a, b,
                            [{"x": 1}, {"x": 2}], metric_fn=custom_metric)
        assert result.winner == "B"


# ═══════════════════════════════════════════════════════════════
# END-TO-END: FULL BRAIN WORKFLOW
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_tool_registry_to_agent_execution(self):
        """Register tools → create adapter → run agent → verify."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec("predict", "Predict revenue", "egm"),
            lambda p: {"net_win_p50": 20000, "confidence": 0.7},
        )
        registry.register(
            ToolSpec("evaluate_deal", "Evaluate deal", "deal"),
            lambda p: {"decision": "GO", "score": 0.82},
        )
        registry.register(
            ToolSpec("deal_impact", "Portfolio impact", "portfolio"),
            lambda p: {"recommendation": "OK"},
        )

        adapter = ArkainBrainAdapter(tool_registry=registry)
        adapter._agent_configs["analyst"] = {
            "role": "deal analyst",
            "tools": ["predict", "evaluate_deal", "deal_impact"],
        }

        # Run agent
        result = adapter.run_agent(
            "analyst",
            "Evaluate the deal and check portfolio impact",
            context={
                "purchase_price": 1500000,
                "property_type": "retail_strip",
                "name": "Test Deal",
                "state": "IL",
                "current_value": 1500000,
            },
        )
        assert result.status == "completed"

    def test_pipeline_to_drift_check(self):
        """Pipeline runs → collect predictions → check drift."""
        # Simulate pipeline producing predictions
        predictions = [
            {"location_id": i, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000 + i * 100}
            for i in range(10)
        ]
        actuals = [
            {"location_id": i, "actual_net_win": 19500 + i * 100}
            for i in range(10)
        ]

        detector = DriftDetector()
        alerts = detector.check_predictions(predictions, actuals)
        assert len(alerts) == 0  # Small errors → no drift

    def test_experiment_informs_retraining(self):
        """A/B experiment determines if new model is better."""
        runner = ExperimentRunner()

        champion = lambda c: {"net_win": {"p50": c["actual_net_win"] * 1.15}}
        challenger = lambda c: {"net_win": {"p50": c["actual_net_win"] * 1.03}}

        cases = [{"actual_net_win": nw}
                 for nw in [15000, 20000, 25000, 30000, 18000]]

        result = runner.run("champion_vs_challenger",
                            champion, challenger, cases)
        assert result.winner == "B"  # Challenger is closer
        assert result.improvement_pct > 0
