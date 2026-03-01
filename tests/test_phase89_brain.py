"""
tests/test_phase89_brain.py — ArkainBrain + Continuous Learning Tests
=========================================================================
Phase 8: Tool registry, agent adapter, pipeline orchestrator
Phase 9: Drift detection, experiment runner, champion/challenger

Run: pytest tests/test_phase89_brain.py -v
"""

import os
import pytest

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
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _mock_registry():
    """Build a registry with mock tools for testing."""
    reg = ToolRegistry()
    reg.register(
        ToolSpec("evaluate_deal", "Evaluate deal", "deal"),
        lambda p: {"decision": "GO", "irr": 0.15, "dscr": 1.3},
    )
    reg.register(
        ToolSpec("portfolio_dashboard", "Portfolio dashboard", "portfolio"),
        lambda p: {"total_assets": 5, "total_value": 3000000},
    )
    reg.register(
        ToolSpec("deal_impact", "Deal impact", "portfolio"),
        lambda p: {"recommendation": "OK", "warnings": []},
    )
    reg.register(
        ToolSpec("simulate_contract", "Monte Carlo", "contract"),
        lambda p: {"irr_p50": 0.18, "operator_cf_p50": 13000},
    )
    reg.register(
        ToolSpec("egm_predict", "Predict EGM", "egm"),
        lambda p: {"net_win": {"p50": 20000}, "confidence": 0.7},
    )
    reg.register(
        ToolSpec("egm_classify", "Classify venue", "egm"),
        lambda p: {"venue_type": "bar"},
    )
    reg.register(
        ToolSpec("amortize", "Amortize loan", "financial"),
        lambda p: {"monthly_payment": 1584.10, "total_interest": 15046.0},
    )
    reg.register(
        ToolSpec("irr", "Compute IRR", "financial"),
        lambda p: {"irr": 0.12},
    )
    reg.register(
        ToolSpec("dscr", "Compute DSCR", "financial"),
        lambda p: {"dscr": 1.25},
    )
    return reg


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRY (Phase 8B)
# ═══════════════════════════════════════════════════════════════

class TestToolRegistry:

    def test_register_and_list(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("t1", "Tool 1", "cat_a"), lambda p: p)
        reg.register(ToolSpec("t2", "Tool 2", "cat_b"), lambda p: p)
        assert len(reg.list_tools()) == 2

    def test_list_by_category(self):
        reg = _mock_registry()
        egm = reg.list_tools("egm")
        fin = reg.list_tools("financial")
        assert len(egm) >= 2
        assert len(fin) >= 2

    def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(
            ToolSpec("add", "Add numbers", "math"),
            lambda p: {"sum": p["a"] + p["b"]},
        )
        result = reg.execute("add", {"a": 3, "b": 4})
        assert result.success
        assert result.data["sum"] == 7
        assert result.execution_ms >= 0

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {})
        assert not result.success
        assert "Unknown tool" in result.error

    def test_execute_handler_error(self):
        reg = ToolRegistry()
        reg.register(
            ToolSpec("boom", "Explode", "test"),
            lambda p: 1 / 0,  # ZeroDivisionError
        )
        result = reg.execute("boom", {})
        assert not result.success
        assert "division" in result.error.lower()

    def test_get_spec(self):
        reg = ToolRegistry()
        spec = ToolSpec("myspec", "My tool", "cat", {"x": "int"})
        reg.register(spec, lambda p: p)
        assert reg.get_spec("myspec") is spec
        assert reg.get_spec("nonexistent") is None

    def test_tool_spec_fields(self):
        spec = ToolSpec(
            "test", "Test tool", "cat",
            requires_approval=True, cost_estimate=0.01,
        )
        assert spec.requires_approval is True
        assert spec.cost_estimate == 0.01


# ═══════════════════════════════════════════════════════════════
# ARKAINBRAIN ADAPTER (Phase 8A)
# ═══════════════════════════════════════════════════════════════

class TestArkainBrainAdapter:

    def test_deal_evaluation_task(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "deal_analyst", "Evaluate this deal property acquisition",
            context={"purchase_price": 1500000, "property_type": "retail_strip"},
        )
        assert result.status == "completed"
        assert len(result.tool_calls) >= 1
        assert result.execution_ms >= 0

    def test_portfolio_task(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "portfolio_analyst", "Show me the portfolio dashboard",
        )
        assert result.status == "completed"
        assert len(result.tool_calls) >= 1

    def test_contract_task(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "contract_analyst", "Simulate this contract Monte Carlo",
            context={"agreement_type": "revenue_share"},
        )
        assert result.status == "completed"

    def test_egm_prediction_task(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "egm_analyst", "Predict gaming revenue for this location",
            context={"venue_type": "bar", "state": "IL", "terminal_count": 5},
        )
        assert result.status == "completed"

    def test_approval_required_blocks(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "deal_analyst", "Big deal",
            require_approval=True,
        )
        assert result.status == "approval_pending"
        assert len(result.tool_calls) == 0

    def test_max_tool_calls_respected(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "analyst", "Do everything",
            context={"purchase_price": 1500000, "property_type": "gas_station"},
            max_tool_calls=1,
        )
        assert len(result.tool_calls) <= 1

    def test_reasoning_trace_populated(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "analyst", "Evaluate this deal",
            context={"purchase_price": 1500000},
        )
        assert len(result.reasoning_trace) > 0

    def test_classify_task(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "classifier", "Classify this venue",
            context={"name": "Joe's Tavern"},
        )
        assert result.status == "completed"

    def test_financial_task(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        result = adapter.run_agent(
            "analyst", "Compute the DSCR for this property",
            context={"noi": 120000, "annual_debt_service": 95000},
        )
        assert result.status == "completed"


# ═══════════════════════════════════════════════════════════════
# PIPELINE ORCHESTRATOR (Phase 8C)
# ═══════════════════════════════════════════════════════════════

class TestPipelineOrchestrator:

    def test_deal_evaluation_pipeline(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("deal_evaluation", {
            "purchase_price": 1500000, "property_type": "gas_station",
            "venue_type": "gas_station", "state": "IL",
            "terminal_count": 5,
        })
        assert result["status"] == "completed"
        assert result["completed_stages"] == result["total_stages"]
        assert result["total_stages"] == 3

    def test_contract_analysis_pipeline(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("contract_analysis", {
            "agreement_type": "revenue_share",
            "venue_type": "bar", "state": "IL",
        })
        assert result["status"] == "completed"
        assert result["total_stages"] == 2

    def test_unknown_pipeline_empty_stages(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("unknown_pipeline", {})
        assert result["total_stages"] == 0
        assert result["status"] == "completed"

    def test_custom_stages(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("custom", {}, stages=[
            {"name": "step1", "agent": "a1",
             "task": "Show portfolio dashboard"},
            {"name": "step2", "agent": "a2",
             "task": "Analyze deal impact on portfolio",
             "requires_approval": False},
        ])
        assert result["total_stages"] == 2

    def test_pipeline_stops_on_approval(self):
        adapter = ArkainBrainAdapter(tool_registry=_mock_registry())
        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("custom", {}, stages=[
            {"name": "auto", "agent": "a1",
             "task": "Show portfolio dashboard"},
            {"name": "manual", "agent": "a2",
             "task": "Approve deal", "requires_approval": True},
            {"name": "after", "agent": "a3",
             "task": "Post-approval step"},
        ])
        assert result["status"] == "incomplete"
        assert "manual" in result["stages"]
        assert result["stages"]["manual"]["status"] == "approval_pending"
        # Third stage should not execute
        assert "after" not in result["stages"]


# ═══════════════════════════════════════════════════════════════
# DRIFT DETECTION (Phase 9B)
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
        detector = DriftDetector(warning_threshold=0.10, critical_threshold=0.25)
        preds = [
            {"location_id": 1, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000},
        ]
        actuals = [{"location_id": 1, "actual_net_win": 17000}]  # 17.6% error
        alerts = detector.check_predictions(preds, actuals)
        assert any(a.severity == "WARNING" for a in alerts)

    def test_critical_drift(self):
        detector = DriftDetector()
        preds = [
            {"location_id": i, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000}
            for i in range(5)
        ]
        actuals = [
            {"location_id": i, "actual_net_win": 10000}
            for i in range(5)
        ]
        alerts = detector.check_predictions(preds, actuals)
        assert any(a.severity == "CRITICAL" for a in alerts)
        assert any(a.alert_type == "prediction_drift" for a in alerts)

    def test_venue_type_drift(self):
        detector = DriftDetector(critical_threshold=0.25)
        preds = [
            {"location_id": 1, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000},
            {"location_id": 2, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 18000},
        ]
        actuals = [
            {"location_id": 1, "actual_net_win": 10000},
            {"location_id": 2, "actual_net_win": 9000},
        ]
        alerts = detector.check_predictions(preds, actuals)
        assert any(a.alert_type == "venue_drift" for a in alerts)

    def test_market_shift_detection(self):
        detector = DriftDetector(market_shift_threshold=0.20)
        current = [
            {"municipality": "Springfield", "total_net_win": 500000},
            {"municipality": "Decatur", "total_net_win": 300000},
        ]
        previous = [
            {"municipality": "Springfield", "total_net_win": 490000},
            {"municipality": "Decatur", "total_net_win": 200000},
        ]
        alerts = detector.check_market_shift(current, previous)
        assert len(alerts) >= 1
        assert alerts[0].alert_type == "market_shift"
        assert "Decatur" in alerts[0].details

    def test_no_market_shift(self):
        detector = DriftDetector()
        current = [{"municipality": "A", "total_net_win": 100000}]
        previous = [{"municipality": "A", "total_net_win": 98000}]
        alerts = detector.check_market_shift(current, previous)
        assert len(alerts) == 0

    def test_empty_inputs(self):
        detector = DriftDetector()
        assert detector.check_predictions([], []) == []
        assert detector.check_market_shift([], []) == []

    def test_alert_to_dict(self):
        alert = DriftAlert(
            alert_type="test", severity="WARNING",
            metric="m1", expected=100, actual=130,
            deviation_pct=0.30, details="test detail",
        )
        d = alert.to_dict()
        assert d["alert_type"] == "test"
        assert d["severity"] == "WARNING"
        assert "timestamp" in d


# ═══════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER (Phase 9C)
# ═══════════════════════════════════════════════════════════════

class TestExperimentRunner:

    def test_basic_experiment(self):
        runner = ExperimentRunner()
        cases = [{"actual_net_win": 18000}] * 10
        result = runner.run(
            "v1_vs_v2",
            variant_a=lambda c: {"net_win": {"p50": 20000}},
            variant_b=lambda c: {"net_win": {"p50": 15000}},
            test_cases=cases,
        )
        assert result.winner == "A"  # 20K closer to 18K
        assert result.improvement_pct > 0
        assert result.sample_size == 10

    def test_variant_b_wins(self):
        runner = ExperimentRunner()
        cases = [{"actual_net_win": 14000}] * 5
        result = runner.run(
            "test",
            variant_a=lambda c: {"net_win": {"p50": 20000}},
            variant_b=lambda c: {"net_win": {"p50": 15000}},
            test_cases=cases,
        )
        assert result.winner == "B"  # 15K closer to 14K

    def test_error_handling(self):
        runner = ExperimentRunner()
        cases = [{"actual_net_win": 10000}] * 3
        result = runner.run(
            "error_test",
            variant_a=lambda c: (_ for _ in ()).throw(ValueError("boom")),
            variant_b=lambda c: {"net_win": {"p50": 10000}},
            test_cases=cases,
        )
        assert result.winner == "B"  # A errors → score 0

    def test_custom_metric(self):
        runner = ExperimentRunner()
        cases = [{"target": 100}] * 5
        result = runner.run(
            "custom",
            variant_a=lambda c: {"value": 95},
            variant_b=lambda c: {"value": 80},
            test_cases=cases,
            metric_fn=lambda r, e: 1.0 - abs(r["value"] - e["target"]) / e["target"],
        )
        assert result.winner == "A"

    def test_empty_cases(self):
        runner = ExperimentRunner()
        result = runner.run(
            "empty", lambda c: {}, lambda c: {}, test_cases=[],
        )
        assert result.sample_size == 0


# ═══════════════════════════════════════════════════════════════
# END-TO-END: Agent → Tools → Pipeline → Drift
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_full_agent_workflow(self):
        """Agent runs tools → collects data → pipeline orchestrator."""
        reg = _mock_registry()
        adapter = ArkainBrainAdapter(tool_registry=reg)

        # Step 1: Predict
        pred = adapter.run_agent(
            "analyst", "Predict gaming revenue for this bar",
            context={"venue_type": "bar", "state": "IL", "terminal_count": 5},
        )
        assert pred.status == "completed"

        # Step 2: Evaluate deal with prediction context
        deal = adapter.run_agent(
            "analyst", "Evaluate this deal acquisition",
            context={"purchase_price": 800000, "property_type": "gas_station"},
        )
        assert deal.status == "completed"

        # Step 3: Check portfolio impact
        impact = adapter.run_agent(
            "analyst", "What is the portfolio impact of this deal",
            context={"name": "New Gas Station", "state": "IL",
                     "current_value": 800000},
        )
        assert impact.status == "completed"

    def test_drift_then_experiment(self):
        """Detect drift → run experiment → pick winner."""
        # Detect drift
        detector = DriftDetector()
        preds = [
            {"location_id": i, "venue_type": "bar", "state": "IL",
             "predicted_net_win": 20000}
            for i in range(10)
        ]
        actuals = [
            {"location_id": i, "actual_net_win": 12000}
            for i in range(10)
        ]
        alerts = detector.check_predictions(preds, actuals)
        assert len(alerts) > 0  # Drift detected

        # Run experiment to compare models
        runner = ExperimentRunner()
        cases = [{"actual_net_win": 12000}] * 10
        result = runner.run(
            "fix_drift",
            variant_a=lambda c: {"net_win": {"p50": 20000}},  # old model
            variant_b=lambda c: {"net_win": {"p50": 13000}},  # retrained
            test_cases=cases,
        )
        assert result.winner == "B"  # Retrained model is closer
