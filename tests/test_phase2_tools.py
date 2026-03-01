"""
tests/test_phase2_tools.py — Financial Tools Phase 2 Tests
=============================================================
Tests for Phase 2: ToolRunnerService, CSV/PDF export,
PolicyBroker registration, and API endpoints.

Run: pytest tests/test_phase2_tools.py -v
"""

import os
import csv
import io
import json
import pytest

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.db.models import Base
from engine.db.repositories import (
    OrganizationRepo, WorkspaceRepo, ToolRunRepo, AuditLogRepo,
)
from engine.tenants import Organization, Workspace
from engine.financial.tools import (
    FINANCIAL_TOOLS,
    amortize, AmortizationInput,
    tvm_solve, TVMInput,
    compute_irr_npv, CashFlowInput,
    compute_dscr, DSCRInput,
    solve_cap_rate, CapRateInput,
)
from engine.financial.serialization import to_dict
from engine.financial.runner import ToolRunnerService, ToolExecutionError
from engine.financial.export import export_csv, export_pdf


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
    OrganizationRepo(session).create(
        Organization(org_id="org1", name="Test Org")
    )
    WorkspaceRepo(session).create(
        Workspace(workspace_id="ws1", org_id="org1", name="Main")
    )
    session.flush()


@pytest.fixture
def runner(session, seed):
    return ToolRunnerService(
        session=session, workspace_id="ws1", user_id="u1",
    )


@pytest.fixture
def runner_no_db():
    """Runner without DB — pure compute only."""
    return ToolRunnerService()


# ═══════════════════════════════════════════════════════════════
# TOOL RUNNER — CORE EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestToolRunnerExecution:

    def test_run_amortization(self, runner):
        result = runner.run("amortization", {
            "principal": 250000,
            "annual_rate": 0.065,
            "term_months": 360,
        })
        assert result["monthly_payment"] > 0
        assert result["total_interest"] > 0
        assert result["actual_term_months"] == 360
        assert "_meta" in result
        assert result["_meta"]["tool_name"] == "amortization"
        assert result["_meta"]["execution_ms"] >= 0

    def test_run_amortization_extra_payments(self, runner):
        result = runner.run("amortization", {
            "principal": 250000,
            "annual_rate": 0.065,
            "term_months": 360,
            "extra_monthly": 200,
        })
        assert result["actual_term_months"] < 360
        assert result["total_interest"] < 300000  # Less with extra payments

    def test_run_tvm_solve_for_pv(self, runner):
        result = runner.run("tvm", {
            "fv": 100000,
            "pmt": 0,
            "rate": 0.005,  # 0.5% per month
            "nper": 120,
        })
        assert result["solved_for"] == "pv"
        assert result["pv"] < 0  # Convention: outflow

    def test_run_tvm_solve_for_pmt(self, runner):
        result = runner.run("tvm", {
            "pv": -250000,
            "fv": 0,
            "rate": 0.065 / 12,
            "nper": 360,
        })
        assert result["solved_for"] == "pmt"
        assert result["pmt"] > 0

    def test_run_irr_npv(self, runner):
        result = runner.run("irr_npv", {
            "cash_flows": [-100000, 25000, 30000, 35000, 40000, 50000],
            "discount_rate": 0.10,
        })
        assert result["irr"] is not None
        assert result["irr"] > 0
        assert result["npv"] is not None
        assert result["payback_period"] is not None

    def test_run_dscr(self, runner):
        result = runner.run("dscr", {
            "noi": 150000,
            "annual_debt_service": 100000,
        })
        assert result["ratio"] == 1.5
        assert result["assessment"] == "strong"

    def test_run_dscr_critical(self, runner):
        result = runner.run("dscr", {
            "noi": 80000,
            "annual_debt_service": 100000,
        })
        assert result["ratio"] == 0.8
        assert result["assessment"] == "critical"

    def test_run_cap_rate_solve_rate(self, runner):
        result = runner.run("cap_rate", {
            "noi": 75000,
            "value": 1000000,
        })
        assert result["solved_for"] == "cap_rate"
        assert result["cap_rate"] == 0.075

    def test_run_cap_rate_solve_value(self, runner):
        result = runner.run("cap_rate", {
            "cap_rate": 0.08,
            "noi": 80000,
        })
        assert result["solved_for"] == "value"
        assert result["value"] == 1000000.0

    def test_run_unknown_tool(self, runner):
        with pytest.raises(ToolExecutionError, match="Unknown tool"):
            runner.run("nonexistent_tool", {})

    def test_run_validation_error(self, runner):
        with pytest.raises(ToolExecutionError, match="Validation"):
            runner.run("amortization", {
                "principal": -100,  # Invalid: negative
                "annual_rate": 0.05,
                "term_months": 360,
            })

    def test_run_without_db(self, runner_no_db):
        """Tools work even without a DB session (pure compute)."""
        result = runner_no_db.run("dscr", {
            "noi": 120000,
            "annual_debt_service": 100000,
        })
        assert result["ratio"] == 1.2
        assert result["assessment"] == "adequate"
        assert result["_meta"]["run_id"] is None


# ═══════════════════════════════════════════════════════════════
# TOOL RUNNER — PERSISTENCE
# ═══════════════════════════════════════════════════════════════

class TestToolRunnerPersistence:

    def test_run_persists_to_tool_runs(self, runner, session):
        runner.run("dscr", {
            "noi": 200000,
            "annual_debt_service": 150000,
        })
        session.flush()

        repo = ToolRunRepo(session)
        runs = repo.list_runs("ws1", tool_name="dscr")
        assert len(runs) >= 1
        latest = runs[0]
        assert latest["tool_name"] == "dscr"
        assert latest["inputs"]["noi"] == 200000
        assert latest["outputs"]["ratio"] > 0
        assert latest["execution_ms"] >= 0

    def test_multiple_runs_persist(self, runner, session):
        for rate in [0.05, 0.06, 0.07, 0.08]:
            runner.run("cap_rate", {"noi": 100000, "value": 100000 / rate})
        session.flush()

        repo = ToolRunRepo(session)
        runs = repo.list_runs("ws1", tool_name="cap_rate")
        assert len(runs) >= 4

    def test_meta_contains_run_id(self, runner, session):
        result = runner.run("dscr", {
            "noi": 100000, "annual_debt_service": 80000,
        })
        session.flush()
        assert result["_meta"]["run_id"] is not None


# ═══════════════════════════════════════════════════════════════
# TOOL RUNNER — BATCH EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestToolRunnerBatch:

    def test_batch_execution(self, runner):
        inputs_list = [
            {"noi": 100000, "annual_debt_service": 80000},
            {"noi": 120000, "annual_debt_service": 80000},
            {"noi": 80000, "annual_debt_service": 80000},
        ]
        results = runner.run_batch("dscr", inputs_list)
        assert len(results) == 3
        assert results[0]["ratio"] == 1.25
        assert results[1]["ratio"] == 1.5
        assert results[2]["ratio"] == 1.0

    def test_batch_partial_failure(self, runner):
        inputs_list = [
            {"noi": 100000, "annual_debt_service": 80000},
            {"noi": 100000, "annual_debt_service": -1},  # Invalid
            {"noi": 50000, "annual_debt_service": 80000},
        ]
        results = runner.run_batch("dscr", inputs_list)
        assert len(results) == 3
        # First and third should succeed
        assert "ratio" in results[0]
        assert "ratio" in results[2]
        # Second should have error
        assert "error" in results[1]["_meta"]

    def test_batch_empty(self, runner):
        results = runner.run_batch("dscr", [])
        assert results == []


# ═══════════════════════════════════════════════════════════════
# TOOL RUNNER — LIST TOOLS
# ═══════════════════════════════════════════════════════════════

class TestToolRunnerListTools:

    def test_list_tools(self, runner):
        tools = runner.list_tools()
        assert len(tools) == len(FINANCIAL_TOOLS)
        names = {t["name"] for t in tools}
        assert "amortization" in names
        assert "irr_npv" in names
        assert "dscr" in names
        assert "cap_rate" in names
        assert "tvm" in names
        assert "sensitivity" in names

    def test_tool_has_field_info(self, runner):
        tools = runner.list_tools()
        dscr_tool = [t for t in tools if t["name"] == "dscr"][0]
        assert "input_fields" in dscr_tool
        assert "output_fields" in dscr_tool
        input_names = {f["name"] for f in dscr_tool["input_fields"]}
        assert "noi" in input_names
        assert "annual_debt_service" in input_names


# ═══════════════════════════════════════════════════════════════
# CSV EXPORT
# ═══════════════════════════════════════════════════════════════

class TestCSVExport:

    def test_amortization_csv(self):
        inp = AmortizationInput(principal=100000, annual_rate=0.06, term_months=12)
        out = amortize(inp)
        csv_bytes = export_csv("amortization", to_dict(inp), to_dict(out))

        text = csv_bytes.decode("utf-8")
        assert "Amortization Schedule" in text
        assert "$100,000.00" in text

        # Parse CSV and verify structure
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        # Should have header, params, summary, and schedule
        assert len(rows) > 15

    def test_generic_tool_csv(self):
        inp = DSCRInput(noi=150000, annual_debt_service=100000)
        out = compute_dscr(inp)
        csv_bytes = export_csv("dscr", to_dict(inp), to_dict(out))

        text = csv_bytes.decode("utf-8")
        assert "dscr" in text
        assert "Inputs" in text
        assert "Outputs" in text
        assert "strong" in text

    def test_irr_npv_csv(self):
        inp = CashFlowInput(
            cash_flows=[-100000, 30000, 35000, 40000, 45000],
            discount_rate=0.10,
        )
        out = compute_irr_npv(inp)
        csv_bytes = export_csv("irr_npv", to_dict(inp), to_dict(out))
        text = csv_bytes.decode("utf-8")
        assert "irr_npv" in text

    def test_csv_is_valid_utf8(self):
        inp = DSCRInput(noi=100000, annual_debt_service=80000)
        out = compute_dscr(inp)
        csv_bytes = export_csv("dscr", to_dict(inp), to_dict(out))
        # Should not raise
        text = csv_bytes.decode("utf-8")
        assert len(text) > 0


# ═══════════════════════════════════════════════════════════════
# PDF EXPORT
# ═══════════════════════════════════════════════════════════════

class TestPDFExport:

    def test_minimal_pdf(self):
        """Test the zero-dependency PDF fallback."""
        inp = DSCRInput(noi=150000, annual_debt_service=100000)
        out = compute_dscr(inp)
        pdf_bytes = export_pdf(
            "dscr", to_dict(inp), to_dict(out),
            title="DSCR Analysis"
        )
        assert pdf_bytes[:5] == b"%PDF-"
        assert b"%%EOF" in pdf_bytes
        assert len(pdf_bytes) > 100

    def test_pdf_with_meta(self):
        inp = CashFlowInput(
            cash_flows=[-50000, 15000, 18000, 20000],
            discount_rate=0.08,
        )
        out = compute_irr_npv(inp)
        meta = {"run_id": 42, "execution_ms": 3}
        pdf_bytes = export_pdf("irr_npv", to_dict(inp), to_dict(out), meta)
        assert pdf_bytes[:5] == b"%PDF-"

    def test_amortization_pdf(self):
        inp = AmortizationInput(principal=100000, annual_rate=0.05, term_months=12)
        out = amortize(inp)
        pdf_bytes = export_pdf("amortization", to_dict(inp), to_dict(out))
        assert pdf_bytes[:5] == b"%PDF-"
        assert len(pdf_bytes) > 200


# ═══════════════════════════════════════════════════════════════
# POLICY REGISTRATION
# ═══════════════════════════════════════════════════════════════

class TestPolicyRegistration:

    def test_register_with_policy_broker(self):
        from engine.policy import PolicyBroker
        from engine.financial.policies import register_financial_policies

        broker = PolicyBroker()
        count = register_financial_policies(broker)
        assert count == len(FINANCIAL_TOOLS)

        # Verify all tools have policies
        for tool_name in FINANCIAL_TOOLS:
            policy = broker.get_policy(tool_name)
            assert policy is not None, f"No policy for {tool_name}"
            assert "read" in policy.allowed_scopes
            assert policy.allow_egress is False
            assert policy.approval == "auto"

    def test_register_db_policies(self, session, seed):
        from engine.financial.policies import register_financial_policies_db
        from engine.db.acp_repositories import ToolPolicyRepo

        count = register_financial_policies_db(session, "ws1")
        session.flush()
        assert count == len(FINANCIAL_TOOLS)

        repo = ToolPolicyRepo(session)
        policies = repo.list_by_workspace("ws1")
        tool_names = {p["tool_name"] for p in policies}
        for name in FINANCIAL_TOOLS:
            assert name in tool_names

    def test_register_db_idempotent(self, session, seed):
        from engine.financial.policies import register_financial_policies_db

        count1 = register_financial_policies_db(session, "ws1")
        session.flush()
        count2 = register_financial_policies_db(session, "ws1")
        session.flush()
        assert count1 == len(FINANCIAL_TOOLS)
        assert count2 == 0  # All already exist


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: RUNNER + EXPORT END-TO-END
# ═══════════════════════════════════════════════════════════════

class TestRunnerExportIntegration:
    """Full pipeline: run tool → persist → export → verify."""

    def test_run_then_csv_export(self, runner, session):
        result = runner.run("amortization", {
            "principal": 200000,
            "annual_rate": 0.055,
            "term_months": 180,
        })
        session.flush()

        # Export from result
        meta = result["_meta"]
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        csv_bytes = export_csv("amortization", {
            "principal": 200000,
            "annual_rate": 0.055,
            "term_months": 180,
        }, clean, meta)

        text = csv_bytes.decode("utf-8")
        assert "$200,000.00" in text
        assert len(text) > 500  # Full schedule

    def test_run_then_pdf_export(self, runner, session):
        result = runner.run("irr_npv", {
            "cash_flows": [-500000, 100000, 120000, 140000, 160000, 180000],
            "discount_rate": 0.12,
        })
        session.flush()

        meta = result["_meta"]
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        pdf_bytes = export_pdf("irr_npv", {
            "cash_flows": [-500000, 100000, 120000, 140000, 160000, 180000],
            "discount_rate": 0.12,
        }, clean, meta, title="Deal IRR Analysis")

        assert pdf_bytes[:5] == b"%PDF-"

    def test_batch_then_verify_all_persisted(self, runner, session):
        inputs = [
            {"noi": noi, "annual_debt_service": 100000}
            for noi in range(80000, 160000, 10000)
        ]
        results = runner.run_batch("dscr", inputs)
        session.flush()

        repo = ToolRunRepo(session)
        runs = repo.list_runs("ws1", tool_name="dscr", limit=100)
        assert len(runs) >= len(inputs)

    def test_run_cap_rate_sensitivity_sweep(self, runner, session):
        """Simulate a cap rate sensitivity analysis via batch."""
        noi_values = [60000, 70000, 80000, 90000, 100000]
        value_values = [800000, 900000, 1000000, 1100000, 1200000]

        inputs = []
        for noi in noi_values:
            for value in value_values:
                inputs.append({"noi": noi, "value": value})

        results = runner.run_batch("cap_rate", inputs)
        assert len(results) == 25  # 5x5

        # All should succeed
        errors = [r for r in results if "error" in r.get("_meta", {})]
        assert len(errors) == 0

        # Verify cap rates make sense
        for r in results:
            assert 0.04 < r["cap_rate"] < 0.15

        session.flush()

    def test_full_deal_analysis_workflow(self, runner, session):
        """End-to-end: amortize → DSCR → IRR → export all."""

        # 1. Amortize the loan
        amort = runner.run("amortization", {
            "principal": 800000,
            "annual_rate": 0.065,
            "term_months": 300,
        })
        annual_debt_service = amort["monthly_payment"] * 12

        # 2. Compute DSCR
        noi = 95000
        dscr = runner.run("dscr", {
            "noi": noi,
            "annual_debt_service": round(annual_debt_service, 2),
        })

        # 3. Compute cap rate
        purchase_price = 1200000
        cap = runner.run("cap_rate", {
            "noi": noi,
            "value": purchase_price,
        })

        # 4. Compute IRR (5-year hold, sell at 5% appreciation)
        annual_cf = noi - annual_debt_service
        sale_price = purchase_price * 1.05 ** 5
        remaining_balance = 0
        for row in amort["schedule"]:
            if row["month"] == 60:
                remaining_balance = row["remaining_balance"]
                break
        sale_proceeds = sale_price - remaining_balance
        equity_in = purchase_price - 800000  # Down payment

        cash_flows = [-equity_in] + [annual_cf] * 4 + [annual_cf + sale_proceeds]
        irr = runner.run("irr_npv", {
            "cash_flows": [round(cf, 2) for cf in cash_flows],
            "discount_rate": 0.10,
        })

        session.flush()

        # Verify deal makes sense
        assert dscr["ratio"] > 1.0, "Deal should cover debt service"
        assert cap["cap_rate"] > 0.05, "Cap rate should be reasonable"
        assert irr["irr"] is not None, "IRR should converge"

        # Export the IRR analysis
        clean_irr = {k: v for k, v in irr.items() if not k.startswith("_")}
        csv_bytes = export_csv("irr_npv", {
            "cash_flows": cash_flows,
            "discount_rate": 0.10,
        }, clean_irr, irr["_meta"])
        assert len(csv_bytes) > 100

        # Verify all 4 runs persisted
        repo = ToolRunRepo(session)
        all_runs = repo.list_runs("ws1", limit=100)
        assert len(all_runs) >= 4


# ═══════════════════════════════════════════════════════════════
# SERIALIZATION
# ═══════════════════════════════════════════════════════════════

class TestSerialization:

    def test_amortization_serialization(self):
        inp = AmortizationInput(principal=100000, annual_rate=0.06, term_months=12)
        out = amortize(inp)
        d = to_dict(out)
        assert isinstance(d, dict)
        assert isinstance(d["schedule"], list)
        assert isinstance(d["schedule"][0], dict)
        # Should be JSON-serializable
        json.dumps(d)

    def test_irr_npv_serialization(self):
        inp = CashFlowInput(cash_flows=[-100, 50, 60, 70], discount_rate=0.1)
        out = compute_irr_npv(inp)
        d = to_dict(out)
        json.dumps(d)  # Must not raise

    def test_sensitivity_input_skips_callable(self):
        from engine.financial.tools import SensitivityInput
        inp = SensitivityInput(
            base_case={"rate": 0.05, "term": 30},
            row_variable="rate",
            row_values=[0.04, 0.05, 0.06],
            col_variable="term",
            col_values=[15, 20, 30],
            compute_fn=lambda p: p["rate"] * p["term"],
        )
        d = to_dict(inp)
        # compute_fn should be excluded
        assert "compute_fn" not in d
        json.dumps(d)


# ═══════════════════════════════════════════════════════════════
# EDGE CASES & REGRESSION
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_zero_rate_amortization(self, runner):
        result = runner.run("amortization", {
            "principal": 12000,
            "annual_rate": 0.0,
            "term_months": 12,
        })
        assert result["monthly_payment"] == 1000.0
        assert result["total_interest"] == 0.0

    def test_single_period_tvm(self, runner):
        result = runner.run("tvm", {
            "pv": -1000,
            "fv": 0,
            "rate": 0.10,
            "nper": 1,
        })
        assert result["solved_for"] == "pmt"
        assert result["pmt"] == 1100.0

    def test_large_cash_flow_series(self, runner):
        """IRR with 30-year monthly cash flows."""
        cfs = [-1000000] + [8000] * 359 + [8000 + 1100000]
        result = runner.run("irr_npv", {
            "cash_flows": cfs,
            "discount_rate": 0.08 / 12,
        })
        assert result["irr"] is not None

    def test_extra_inputs_ignored(self, runner):
        """Extra fields in inputs dict should be silently ignored."""
        result = runner.run("dscr", {
            "noi": 100000,
            "annual_debt_service": 80000,
            "random_extra_field": "should be ignored",
        })
        assert result["ratio"] == 1.25
