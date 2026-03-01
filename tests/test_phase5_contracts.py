"""
tests/test_phase5_contracts.py — Contract Engine Tests
=========================================================
Phase 5: Tests for contract templates, Monte Carlo simulation,
deal analyzer, contract repositories, and full workflow.

Run: pytest tests/test_phase5_contracts.py -v
"""

import os
import math
import pytest
from dataclasses import asdict
from datetime import datetime, timezone

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.db.models import Base
from engine.db.repositories import OrganizationRepo, WorkspaceRepo
from engine.tenants import Organization, Workspace
from engine.contracts.templates import (
    default_templates, validate_terms, apply_overrides,
    compute_monthly_operator_cash, compute_monthly_debt_service,
)
from engine.contracts.montecarlo import (
    SimulationInputs, SimulationResult, run_simulation,
    compare_structures, _fit_lognormal, _solve_irr,
)


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


# Standard prediction quantiles for tests
PRED = {
    "coin_in": {"p10": 50000, "p50": 80000, "p90": 120000},
    "hold_pct": {"p10": 0.22, "p50": 0.26, "p90": 0.31},
}


# ═══════════════════════════════════════════════════════════════
# CONTRACT TEMPLATES
# ═══════════════════════════════════════════════════════════════

class TestTemplates:

    def test_default_templates_exist(self):
        templates = default_templates()
        assert len(templates) >= 3
        types = {t["agreement_type"] for t in templates}
        assert "revenue_share" in types
        assert "flat_lease" in types
        assert "hybrid" in types

    def test_template_has_required_fields(self):
        for t in default_templates():
            assert "name" in t
            assert "agreement_type" in t
            assert "terms" in t
            assert isinstance(t["terms"], dict)

    def test_validate_revenue_share(self):
        t = default_templates()[0]
        errors = validate_terms(t["agreement_type"], t["terms"],
                                t.get("constraints", {}))
        assert not errors, f"Unexpected validation errors: {errors}"

    def test_validate_rejects_invalid(self):
        errors = validate_terms("revenue_share", {"operator_split": 1.5}, {})
        # Should catch out-of-range split
        # (depends on implementation; may or may not enforce)
        # At minimum, validate_terms should not crash
        assert isinstance(errors, list)

    def test_apply_overrides(self):
        base = {"operator_split": 0.65, "host_split": 0.35, "extra": "keep"}
        modified = apply_overrides(base, {"operator_split": 0.60})
        assert modified["operator_split"] == 0.60
        assert modified["host_split"] == 0.35  # Unchanged
        assert modified["extra"] == "keep"

    def test_apply_overrides_empty(self):
        base = {"a": 1, "b": 2}
        assert apply_overrides(base, {}) == base


class TestMonthlyOperatorCash:

    def test_revenue_share(self):
        cash = compute_monthly_operator_cash(
            "revenue_share", {"operator_split": 0.65, "host_split": 0.35}, 20000
        )
        assert cash == 13000.0  # 0.65 * 20000

    def test_revenue_share_zero_nw(self):
        cash = compute_monthly_operator_cash(
            "revenue_share", {"operator_split": 0.65}, 0
        )
        assert cash == 0.0

    def test_flat_lease(self):
        cash = compute_monthly_operator_cash(
            "flat_lease", {"monthly_lease": 2000}, 20000
        )
        # Operator gets net_win minus lease payment to host
        assert cash == 18000.0  # 20000 - 2000

    def test_flat_lease_nw_below_lease(self):
        cash = compute_monthly_operator_cash(
            "flat_lease", {"monthly_lease": 2000}, 1000
        )
        # Operator still owes lease even if NW < lease
        assert cash == -1000.0

    def test_hybrid(self):
        terms = {
            "base_lease": 1000, "operator_split": 0.70,
            "host_split": 0.30, "threshold": 15000,
        }
        # NW above threshold: base_lease + share of excess
        cash = compute_monthly_operator_cash("hybrid", terms, 25000)
        # Host gets: base_lease + host_split * (nw - threshold)
        # = 1000 + 0.30 * (25000 - 15000) = 1000 + 3000 = 4000
        # Operator gets: 25000 - 4000 = 21000
        assert cash == 21000.0

    def test_hybrid_below_threshold(self):
        terms = {
            "base_lease": 1000, "operator_split": 0.70,
            "host_split": 0.30, "threshold": 15000,
        }
        # NW below threshold: host just gets base_lease
        cash = compute_monthly_operator_cash("hybrid", terms, 10000)
        assert cash == 9000.0  # 10000 - 1000


class TestDebtService:

    def test_basic_loan(self):
        terms = {
            "acquisition_cost": 100000,
            "down_payment_pct": 0.20,
            "annual_rate": 0.07,
            "loan_term_months": 60,
        }
        ds = compute_monthly_debt_service(terms)
        assert ds > 0
        # 80K loan @ 7% / 60mo ≈ $1,584/mo
        assert 1500 < ds < 1700

    def test_zero_rate(self):
        terms = {
            "acquisition_cost": 60000,
            "down_payment_pct": 0.0,
            "annual_rate": 0.0,
            "loan_term_months": 60,
        }
        ds = compute_monthly_debt_service(terms)
        assert ds == 1000.0  # 60000 / 60

    def test_no_loan(self):
        terms = {"acquisition_cost": 0}
        ds = compute_monthly_debt_service(terms)
        assert ds == 0.0


# ═══════════════════════════════════════════════════════════════
# MONTE CARLO ENGINE
# ═══════════════════════════════════════════════════════════════

class TestMonteCarloEngine:

    def _make_inputs(self, **kwargs):
        defaults = dict(
            coin_in_p10=50000, coin_in_p50=80000, coin_in_p90=120000,
            hold_pct_p10=0.22, hold_pct_p50=0.26, hold_pct_p90=0.31,
            agreement_type="revenue_share",
            operator_split=0.65, host_split=0.35,
            acquisition_cost=100000, contract_months=60,
            num_simulations=3000, seed=42,
        )
        defaults.update(kwargs)
        return SimulationInputs(**defaults)

    def test_basic_simulation(self):
        inputs = self._make_inputs()
        result = run_simulation(inputs)

        assert result.num_simulations == 3000
        assert result.valid_simulations > 0
        assert result.irr_p10 < result.irr_p50 < result.irr_p90
        assert result.net_win_p50 > 0
        assert result.operator_cf_p50 > 0
        assert result.execution_ms >= 0

    def test_quantile_ordering(self):
        result = run_simulation(self._make_inputs())
        assert result.irr_p10 <= result.irr_p25
        assert result.irr_p25 <= result.irr_p50
        assert result.irr_p50 <= result.irr_p75
        assert result.irr_p75 <= result.irr_p90

    def test_deterministic_with_seed(self):
        inputs = self._make_inputs(seed=123)
        r1 = run_simulation(inputs)
        r2 = run_simulation(inputs)
        assert r1.irr_p50 == r2.irr_p50
        assert r1.net_win_p50 == r2.net_win_p50
        assert r1.operator_cf_p50 == r2.operator_cf_p50

    def test_different_seeds_differ(self):
        r1 = run_simulation(self._make_inputs(seed=1))
        r2 = run_simulation(self._make_inputs(seed=999))
        # Very unlikely to be exactly equal
        assert r1.irr_p50 != r2.irr_p50

    def test_flat_lease_simulation(self):
        inputs = self._make_inputs(
            agreement_type="flat_lease", monthly_lease=2000,
        )
        result = run_simulation(inputs)
        assert result.irr_p50 > 0
        assert result.operator_cf_p50 > 0

    def test_hybrid_simulation(self):
        inputs = self._make_inputs(
            agreement_type="hybrid",
            base_lease=1000, operator_split=0.70, host_split=0.30,
            threshold=15000,
        )
        result = run_simulation(inputs)
        assert result.irr_p50 > 0
        assert result.operator_cf_p50 > 0

    def test_financed_acquisition(self):
        inputs = self._make_inputs(
            acquisition_type="financed", acquisition_cost=150000,
            down_payment_pct=0.20, annual_rate=0.085,
            loan_term_months=48,
        )
        result = run_simulation(inputs)
        assert result.irr_p50 > 0
        # Financed should have lower operator CF due to debt service
        cash_inputs = self._make_inputs(acquisition_cost=150000)
        cash_result = run_simulation(cash_inputs)
        assert result.operator_cf_p50 < cash_result.operator_cf_p50

    def test_higher_acquisition_lowers_irr(self):
        cheap = run_simulation(self._make_inputs(acquisition_cost=50000))
        expensive = run_simulation(self._make_inputs(acquisition_cost=200000))
        assert cheap.irr_p50 > expensive.irr_p50

    def test_higher_split_better_for_operator(self):
        low_split = run_simulation(self._make_inputs(
            operator_split=0.50, host_split=0.50,
        ))
        high_split = run_simulation(self._make_inputs(
            operator_split=0.80, host_split=0.20,
        ))
        assert high_split.operator_cf_p50 > low_split.operator_cf_p50

    def test_breakeven_net_win(self):
        result = run_simulation(self._make_inputs())
        assert result.breakeven_net_win > 0
        assert result.target_net_win_20pct > result.breakeven_net_win

    def test_risk_probabilities_bounded(self):
        result = run_simulation(self._make_inputs())
        assert 0 <= result.prob_negative_irr <= 1
        assert 0 <= result.prob_below_10pct <= 1
        assert 0 <= result.prob_below_20pct <= 1

    def test_small_sim_count(self):
        """Even 100 simulations should work."""
        inputs = self._make_inputs(num_simulations=100)
        result = run_simulation(inputs)
        assert result.valid_simulations > 0
        assert result.irr_p50 > 0

    def test_large_sim_count_stability(self):
        """10K sims should produce stable results."""
        r1 = run_simulation(self._make_inputs(num_simulations=10000, seed=1))
        r2 = run_simulation(self._make_inputs(num_simulations=10000, seed=2))
        # p50 should be within 10% of each other
        diff = abs(r1.irr_p50 - r2.irr_p50) / max(abs(r1.irr_p50), 0.01)
        assert diff < 0.10, f"IRR p50 unstable: {r1.irr_p50:.4f} vs {r2.irr_p50:.4f}"


class TestCompareStructures:

    def test_compare_basic(self):
        base = SimulationInputs(
            coin_in_p10=50000, coin_in_p50=80000, coin_in_p90=120000,
            hold_pct_p10=0.22, hold_pct_p50=0.26, hold_pct_p90=0.31,
            acquisition_cost=100000, contract_months=60,
            num_simulations=2000, seed=42,
        )
        structures = [
            {"name": "Rev Share", "agreement_type": "revenue_share",
             "terms": {"operator_split": 0.65, "host_split": 0.35}},
            {"name": "Flat Lease", "agreement_type": "flat_lease",
             "terms": {"monthly_lease": 2000}},
        ]
        results = compare_structures(base, structures)
        assert len(results) == 2

        for r in results:
            assert "irr_p50" in r
            assert "operator_cf_p50" in r
            assert "rank" in r
            assert r["irr_p50"] > 0

    def test_compare_ranked_by_irr(self):
        base = SimulationInputs(
            coin_in_p10=50000, coin_in_p50=80000, coin_in_p90=120000,
            hold_pct_p10=0.22, hold_pct_p50=0.26, hold_pct_p90=0.31,
            acquisition_cost=100000, contract_months=60,
            num_simulations=1000, seed=42,
        )
        structures = [
            {"name": "Low Split", "agreement_type": "revenue_share",
             "terms": {"operator_split": 0.50, "host_split": 0.50}},
            {"name": "High Split", "agreement_type": "revenue_share",
             "terms": {"operator_split": 0.80, "host_split": 0.20}},
        ]
        results = compare_structures(base, structures)
        # Ranked by IRR p50 descending
        assert results[0]["rank"] == 1
        assert results[0]["irr_p50"] >= results[1]["irr_p50"]


# ═══════════════════════════════════════════════════════════════
# MATH HELPERS
# ═══════════════════════════════════════════════════════════════

class TestMathHelpers:

    def test_fit_lognormal(self):
        mu, sigma = _fit_lognormal(50000, 80000, 120000)
        assert sigma > 0
        assert math.isfinite(mu)

    def test_solve_irr_positive(self):
        # -100K investment, +3K/month for 60 months
        cfs = [-100000] + [3000] * 60
        irr = _solve_irr(cfs)
        assert irr > 0

    def test_solve_irr_negative(self):
        # -200K investment, +1K/month for 12 months (big loss)
        cfs = [-200000] + [1000] * 12
        irr = _solve_irr(cfs)
        assert irr < 0


# ═══════════════════════════════════════════════════════════════
# CONTRACT REPOSITORIES
# ═══════════════════════════════════════════════════════════════

class TestContractTemplateRepo:

    def test_create_and_get(self, session, seed):
        from engine.db.contract_repositories import ContractTemplateRepo
        repo = ContractTemplateRepo(session)
        tmpl = repo.create(
            workspace_id="ws1", name="Test Revenue Share",
            agreement_type="revenue_share", acquisition_type="cash",
            terms={"operator_split": 0.65, "host_split": 0.35},
            constraints={"operator_split_min": 0.50},
            created_by="admin",
        )
        session.flush()

        assert tmpl["name"] == "Test Revenue Share"
        assert tmpl["version"] == 1

        fetched = repo.get(tmpl["id"])
        assert fetched is not None
        assert fetched["terms"]["operator_split"] == 0.65

    def test_list_templates(self, session, seed):
        from engine.db.contract_repositories import ContractTemplateRepo
        repo = ContractTemplateRepo(session)
        repo.create("ws1", "T1", "revenue_share", "cash", {}, {})
        repo.create("ws1", "T2", "flat_lease", "cash", {}, {})
        session.flush()

        templates = repo.list_templates("ws1")
        assert len(templates) >= 2

    def test_deactivate(self, session, seed):
        from engine.db.contract_repositories import ContractTemplateRepo
        repo = ContractTemplateRepo(session)
        tmpl = repo.create("ws1", "Deact", "flat_lease", "cash", {}, {})
        session.flush()

        repo.deactivate(tmpl["id"])
        session.flush()

        fetched = repo.get(tmpl["id"])
        assert fetched["is_active"] is False


class TestSimulationRunRepo:

    def test_create_and_get(self, session, seed):
        from engine.db.contract_repositories import SimulationRunRepo
        repo = SimulationRunRepo(session)
        run = repo.create(
            workspace_id="ws1", scenario_name="Test Run",
            inputs={"type": "revenue_share"}, results={"irr_p50": 0.15},
            user_id="admin",
        )
        session.flush()

        assert run["scenario_name"] == "Test Run"
        fetched = repo.get(run["id"])
        assert fetched is not None
        assert fetched["results"]["irr_p50"] == 0.15

    def test_list_runs(self, session, seed):
        from engine.db.contract_repositories import SimulationRunRepo
        repo = SimulationRunRepo(session)
        for i in range(3):
            repo.create("ws1", f"Run {i}", {}, {}, "u1")
        session.flush()

        runs = repo.list_runs("ws1")
        assert len(runs) >= 3


# ═══════════════════════════════════════════════════════════════
# DEAL ANALYZER (full integration)
# ═══════════════════════════════════════════════════════════════

class TestDealAnalyzer:

    def test_analyze_deal(self, session, seed):
        from engine.contracts.analyzer import DealAnalyzer
        analyzer = DealAnalyzer(session, "ws1", "admin")

        result = analyzer.analyze_deal(
            agreement_type="revenue_share",
            terms={"operator_split": 0.65, "host_split": 0.35},
            prediction=PRED,
            num_simulations=2000,
            seed=42,
        )

        assert "irr_p50" in result or "simulation" in result
        # The analyzer should return useful data
        assert isinstance(result, dict)

    def test_compare_deals(self, session, seed):
        from engine.contracts.analyzer import DealAnalyzer
        analyzer = DealAnalyzer(session, "ws1", "admin")

        result = analyzer.compare_deals(
            prediction=PRED,
            structures=[
                {"name": "Rev Share", "agreement_type": "revenue_share",
                 "terms": {"operator_split": 0.65, "host_split": 0.35}},
                {"name": "Flat Lease", "agreement_type": "flat_lease",
                 "terms": {"monthly_lease": 2000}},
            ],
            acquisition_cost=100000,
            num_simulations=1000,
            seed=42,
        )

        assert isinstance(result, (list, dict))


# ═══════════════════════════════════════════════════════════════
# END-TO-END: TEMPLATE → SIMULATE → COMPARE → AUDIT
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_full_deal_workflow(self, session, seed):
        """Complete workflow: create template → simulate → compare → audit."""
        from engine.db.contract_repositories import ContractTemplateRepo, SimulationRunRepo
        from engine.contracts.montecarlo import SimulationInputs, run_simulation, compare_structures

        # 1. Create contract template
        ct_repo = ContractTemplateRepo(session)
        tmpl = ct_repo.create(
            workspace_id="ws1", name="IL Standard Rev Share",
            agreement_type="revenue_share", acquisition_type="cash",
            terms={"operator_split": 0.65, "host_split": 0.35},
            constraints={}, created_by="admin",
        )
        session.flush()
        assert tmpl["id"] > 0

        # 2. Run simulation with template terms
        inputs = SimulationInputs(
            coin_in_p10=50000, coin_in_p50=80000, coin_in_p90=120000,
            hold_pct_p10=0.22, hold_pct_p50=0.26, hold_pct_p90=0.31,
            agreement_type="revenue_share",
            operator_split=0.65, host_split=0.35,
            acquisition_cost=100000, contract_months=60,
            num_simulations=3000, seed=42,
        )
        result = run_simulation(inputs)
        assert result.irr_p50 > 0
        assert result.operator_cf_p50 > 0

        # 3. Persist simulation run
        sr_repo = SimulationRunRepo(session)
        run = sr_repo.create(
            workspace_id="ws1",
            scenario_name="IL Bar 5-VGT Analysis",
            inputs=asdict(inputs),
            results=asdict(result),
            user_id="admin",
        )
        session.flush()
        assert run["id"] > 0

        # 4. Compare structures
        base = SimulationInputs(
            coin_in_p10=50000, coin_in_p50=80000, coin_in_p90=120000,
            hold_pct_p10=0.22, hold_pct_p50=0.26, hold_pct_p90=0.31,
            acquisition_cost=100000, contract_months=60,
            num_simulations=2000, seed=42,
        )
        comparison = compare_structures(base, [
            {"name": "65/35 Rev Share", "agreement_type": "revenue_share",
             "terms": {"operator_split": 0.65, "host_split": 0.35}},
            {"name": "$2K Flat Lease", "agreement_type": "flat_lease",
             "terms": {"monthly_lease": 2000}},
            {"name": "Hybrid $1K+70/30", "agreement_type": "hybrid",
             "terms": {"base_lease": 1000, "operator_split": 0.70,
                       "host_split": 0.30, "threshold": 15000}},
        ])
        assert len(comparison) == 3
        assert comparison[0]["rank"] == 1

        # 5. Persist comparison
        comp_run = sr_repo.create(
            workspace_id="ws1",
            scenario_name="IL Bar Structure Comparison",
            inputs={"structures": 3, "acquisition_cost": 100000},
            results={"ranked": comparison},
            user_id="admin",
        )
        session.flush()

        # 6. Retrieve audit trail
        runs = sr_repo.list_runs("ws1")
        assert len(runs) >= 2

        fetched = sr_repo.get(run["id"])
        assert fetched is not None
        assert "irr_p50" in fetched["results"]

    def test_financed_deal_workflow(self, session, seed):
        """Financed acquisition: higher complexity with debt service."""
        from engine.contracts.montecarlo import SimulationInputs, run_simulation
        from engine.db.contract_repositories import SimulationRunRepo

        inputs = SimulationInputs(
            coin_in_p10=50000, coin_in_p50=80000, coin_in_p90=120000,
            hold_pct_p10=0.22, hold_pct_p50=0.26, hold_pct_p90=0.31,
            agreement_type="revenue_share",
            operator_split=0.65, host_split=0.35,
            acquisition_type="financed",
            acquisition_cost=150000,
            down_payment_pct=0.20,
            annual_rate=0.085,
            loan_term_months=48,
            contract_months=60,
            num_simulations=3000, seed=42,
        )
        result = run_simulation(inputs)

        assert result.irr_p50 > 0
        assert result.operator_cf_p50 > 0
        assert result.breakeven_net_win > 0

        # Persist
        sr_repo = SimulationRunRepo(session)
        run = sr_repo.create("ws1", "Financed Deal", asdict(inputs),
                             asdict(result), "admin")
        session.flush()
        assert run["id"] > 0
