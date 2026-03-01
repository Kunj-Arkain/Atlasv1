"""
tests/test_phase6_realestate.py — Real Estate Capital Filter Tests
=====================================================================
Phase 6: Tests for property templates, 7-stage pipeline,
deal evaluation, gaming integration, repositories, and full workflow.

Run: pytest tests/test_phase6_realestate.py -v
"""

import os
import pytest
from datetime import datetime, timezone

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.db.models import Base
from engine.db.repositories import OrganizationRepo, WorkspaceRepo
from engine.tenants import Organization, Workspace
from engine.realestate.templates import (
    default_property_templates, get_template_for_type,
)
from engine.realestate.stages import (
    stage_intake, stage_feasibility, stage_market,
    stage_cost, stage_finance, stage_risk, stage_decision,
)
from engine.realestate.pipeline import DealPipeline


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


GOOD_DEAL = {
    "deal_name": "123 Main St Strip Center",
    "property_type": "retail_strip",
    "purchase_price": 1500000,
    "noi": 120000,
    "address": "123 Main St, Springfield, IL",
    "state": "IL",
}

GAS_STATION_DEAL = {
    "deal_name": "BP Gas Station",
    "property_type": "gas_station",
    "purchase_price": 800000,
    "noi": 72000,
    "address": "456 Route 66, Decatur, IL",
    "state": "IL",
    "terminal_count": 5,
}

GAMING_PREDICTION = {
    "coin_in": {"p10": 50000, "p50": 80000, "p90": 120000},
    "hold_pct": {"p10": 0.22, "p50": 0.26, "p90": 0.31},
    "net_win": {"p10": 11000, "p50": 20800, "p90": 37200},
}


# ═══════════════════════════════════════════════════════════════
# PROPERTY TEMPLATES
# ═══════════════════════════════════════════════════════════════

class TestPropertyTemplates:

    def test_default_templates_exist(self):
        templates = default_property_templates()
        assert len(templates) >= 5
        types = {t["property_type"] for t in templates}
        assert "retail_strip" in types
        assert "gas_station" in types
        assert "qsr" in types
        assert "dollar" in types
        assert "shopping_center" in types

    def test_template_has_scoring_weights(self):
        for t in default_property_templates():
            assert "scoring_weights" in t
            weights = t["scoring_weights"]
            assert isinstance(weights, dict)
            assert len(weights) > 0

    def test_get_template_by_type(self):
        gas = get_template_for_type("gas_station")
        assert gas is not None
        assert gas["property_type"] == "gas_station"

    def test_get_template_unknown_returns_none(self):
        result = get_template_for_type("underwater_fortress")
        assert result is None

    def test_gas_station_has_gaming_weight(self):
        gas = get_template_for_type("gas_station")
        assert "gaming_upside" in gas.get("scoring_weights", {})


# ═══════════════════════════════════════════════════════════════
# PIPELINE STAGES
# ═══════════════════════════════════════════════════════════════

class TestStageIntake:

    def test_valid_input(self):
        result = stage_intake(GOOD_DEAL, {})
        assert result["status"] == "pass"
        assert result["purchase_price"] == 1500000
        assert result["noi"] == 120000

    def test_missing_price_fails(self):
        result = stage_intake({"address": "123 Main"}, {})
        assert result["status"] == "fail"
        assert any("purchase_price" in e for e in result["errors"])

    def test_derives_noi_from_cap_rate(self):
        result = stage_intake(
            {"purchase_price": 1000000, "address": "test"},
            {"cap_rate": 0.08},
        )
        assert result["status"] == "pass"
        assert result["noi"] == 80000  # 1M * 0.08
        assert result["params"]["noi_source"] == "derived_from_cap_rate"

    def test_user_provided_noi_preserved(self):
        result = stage_intake(GOOD_DEAL, {})
        assert result["params"]["noi_source"] == "user_provided"

    def test_defaults_merged(self):
        result = stage_intake(
            {"purchase_price": 500000, "address": "test"},
            {"cap_rate": 0.07, "vacancy_rate": 0.05},
        )
        assert result["params"]["vacancy_rate"] == 0.05


class TestStageFeasibility:

    def test_standard_property_passes(self):
        intake = stage_intake(GOOD_DEAL, {})
        result = stage_feasibility(intake["params"])
        assert result["status"] == "pass"
        assert result["score"] > 0

    def test_score_is_bounded(self):
        intake = stage_intake(GOOD_DEAL, {})
        result = stage_feasibility(intake["params"])
        assert 0 <= result["score"] <= 1.0


class TestStageMarket:

    def test_market_runs(self):
        intake = stage_intake(GOOD_DEAL, {})
        result = stage_market(intake["params"])
        assert result["status"] == "pass"
        assert "score" in result

    def test_market_with_context(self):
        intake = stage_intake(GOOD_DEAL, {})
        ctx = {"gaming_location_count": 47, "avg_nti": 18000}
        result = stage_market(intake["params"], market_context=ctx)
        assert result["status"] == "pass"


class TestStageCost:

    def test_cost_runs(self):
        intake = stage_intake(GOOD_DEAL, {})
        result = stage_cost(intake["params"])
        assert result["status"] == "pass"
        assert "total_capex" in result


class TestStageFinance:

    def test_finance_computes_metrics(self):
        intake = stage_intake(GOOD_DEAL, {})
        result = stage_finance(intake["params"])
        assert result["status"] == "pass"
        assert "dscr" in result
        assert "cap_rate" in result
        assert result["dscr"] > 0
        assert result["cap_rate"] > 0

    def test_high_noi_good_dscr(self):
        intake = stage_intake({
            **GOOD_DEAL, "noi": 200000,
        }, {})
        result = stage_finance(intake["params"])
        assert result["dscr"] > 1.0

    def test_low_noi_poor_metrics(self):
        intake = stage_intake({
            **GOOD_DEAL, "noi": 30000,
        }, {})
        result = stage_finance(intake["params"])
        assert result["dscr"] < 1.0 or result["cap_rate"] < 0.05


class TestStageRisk:

    def test_risk_runs(self):
        intake = stage_intake(GOOD_DEAL, {})
        fin = stage_finance(intake["params"])
        result = stage_risk(intake["params"], fin)
        assert result["status"] == "pass"


class TestStageDecision:

    def test_go_decision(self):
        # High scores everywhere → GO
        scores = {
            "feasibility": 1.0, "market_strength": 0.8,
            "cost_risk": 0.9, "financial_return": 0.9,
            "debt_coverage": 0.8, "gaming_upside": 0.7,
        }
        weights = {
            "feasibility": 0.15, "market_strength": 0.20,
            "cost_risk": 0.10, "financial_return": 0.30,
            "debt_coverage": 0.15, "gaming_upside": 0.10,
        }
        fin = {"irr": 0.12, "dscr": 1.5}
        risk = {"risk_score": 0.3}
        result = stage_decision(scores, weights, fin, risk)
        assert result["decision"] in ("GO", "HOLD")

    def test_no_go_decision(self):
        scores = {
            "feasibility": 0.1, "market_strength": 0.2,
            "cost_risk": 0.1, "financial_return": 0.1,
            "debt_coverage": 0.1, "gaming_upside": 0.0,
        }
        weights = {
            "feasibility": 0.15, "market_strength": 0.20,
            "cost_risk": 0.10, "financial_return": 0.30,
            "debt_coverage": 0.15, "gaming_upside": 0.10,
        }
        result = stage_decision(scores, weights, {"irr": 0.02}, {"risk_score": 0.9})
        assert result["decision"] in ("NO_GO", "HOLD")


# ═══════════════════════════════════════════════════════════════
# DEAL PIPELINE (in-memory, no DB)
# ═══════════════════════════════════════════════════════════════

class TestDealPipeline:

    def test_evaluate_good_deal(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate(GOOD_DEAL)
        assert result["decision"] in ("GO", "HOLD", "NO_GO")
        assert "stage_results" in result
        assert "intake" in result["stage_results"]
        assert "finance" in result["stage_results"]
        assert "decision" in result["stage_results"]

    def test_evaluate_all_stages_run(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate(GOOD_DEAL)
        stages = result["stage_results"]
        expected = ["intake", "feasibility", "market", "cost",
                    "finance", "risk", "decision"]
        for s in expected:
            assert s in stages, f"Missing stage: {s}"

    def test_evaluate_returns_scores(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate(GOOD_DEAL)
        assert "scores" in result
        scores = result["scores"]
        assert len(scores) > 0

    def test_missing_fields_returns_no_go(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate({"property_type": "retail_strip"})
        assert result["decision"] == "NO_GO"

    def test_different_property_types(self):
        pipeline = DealPipeline()
        for ptype in ["retail_strip", "gas_station", "qsr", "dollar"]:
            result = pipeline.evaluate({
                **GOOD_DEAL, "property_type": ptype,
            })
            assert result["decision"] in ("GO", "HOLD", "NO_GO")

    def test_overpriced_deal_not_go(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate({
            **GOOD_DEAL,
            "purchase_price": 10000000,  # $10M for $120K NOI = 1.2% cap
            "noi": 120000,
        })
        # Should be HOLD or NO_GO — not a clean GO
        assert result["decision"] in ("HOLD", "NO_GO")


class TestDealPipelineGaming:

    def test_evaluate_with_gaming(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate_with_gaming(
            inputs=GAS_STATION_DEAL,
            gaming_prediction=GAMING_PREDICTION,
        )
        assert result["decision"] in ("GO", "HOLD", "NO_GO")
        # Gaming should add to the deal value
        intake = result["stage_results"]["intake"]
        assert intake["params"].get("gaming_eligible") is True
        assert intake["params"].get("expected_gaming_net_win_monthly", 0) > 0

    def test_gaming_improves_score(self):
        """Gas station with gaming should score at least as well as without."""
        pipeline = DealPipeline()
        without = pipeline.evaluate(GAS_STATION_DEAL)
        with_gaming = pipeline.evaluate_with_gaming(
            inputs=GAS_STATION_DEAL.copy(),
            gaming_prediction=GAMING_PREDICTION,
        )
        # Gaming upside score should be higher with prediction
        w_score = with_gaming.get("scores", {}).get("gaming_upside", 0)
        wo_score = without.get("scores", {}).get("gaming_upside", 0)
        assert w_score >= wo_score

    def test_no_prediction_still_works(self):
        pipeline = DealPipeline()
        result = pipeline.evaluate_with_gaming(
            inputs=GAS_STATION_DEAL, gaming_prediction=None,
        )
        assert result["decision"] in ("GO", "HOLD", "NO_GO")


# ═══════════════════════════════════════════════════════════════
# REPOSITORIES
# ═══════════════════════════════════════════════════════════════

class TestPropertyTemplateRepo:

    def test_create_and_get(self, session, seed):
        from engine.db.deal_repositories import PropertyTemplateRepo
        repo = PropertyTemplateRepo(session)
        tmpl = repo.create(
            workspace_id="ws1", name="Test Gas Station",
            property_type="gas_station",
            defaults={"cap_rate": 0.08},
            scoring_weights={"financial_return": 0.30},
        )
        session.flush()

        assert tmpl["property_type"] == "gas_station"
        fetched = repo.get(tmpl["id"])
        assert fetched is not None

    def test_list_templates(self, session, seed):
        from engine.db.deal_repositories import PropertyTemplateRepo
        repo = PropertyTemplateRepo(session)
        repo.create("ws1", "T1", "retail_strip", {}, {})
        repo.create("ws1", "T2", "qsr", {}, {})
        session.flush()

        templates = repo.list_templates("ws1")
        assert len(templates) >= 2

    def test_get_by_type(self, session, seed):
        from engine.db.deal_repositories import PropertyTemplateRepo
        repo = PropertyTemplateRepo(session)
        repo.create("ws1", "Gas Template", "gas_station", {}, {})
        session.flush()

        result = repo.get_by_type("ws1", "gas_station")
        assert result is not None
        assert result["property_type"] == "gas_station"


class TestDealRunRepo:

    def test_create_and_get(self, session, seed):
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(session)
        run = repo.create(
            workspace_id="ws1", deal_name="Test Deal",
            property_type="retail_strip",
            inputs={"purchase_price": 1500000},
            user_id="admin",
        )
        session.flush()

        assert run["deal_name"] == "Test Deal"
        fetched = repo.get(run["id"])
        assert fetched is not None

    def test_complete_run(self, session, seed):
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(session)
        run = repo.create("ws1", "Complete Test", "qsr",
                          {"price": 500000}, "admin")
        session.flush()

        repo.complete(
            run["id"],
            stage_results={"intake": {"status": "pass"}},
            scores={"financial_return": 0.85},
            decision="GO",
            explanation="Solid deal",
        )
        session.flush()

        fetched = repo.get(run["id"])
        assert fetched["status"] == "completed"
        assert fetched["decision"] == "GO"

    def test_fail_run(self, session, seed):
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(session)
        run = repo.create("ws1", "Fail Test", "dollar", {}, "admin")
        session.flush()

        repo.fail(run["id"])
        session.flush()

        fetched = repo.get(run["id"])
        assert fetched["status"] == "failed"

    def test_list_runs(self, session, seed):
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(session)
        for i in range(3):
            repo.create("ws1", f"Deal {i}", "retail_strip", {}, "admin")
        session.flush()

        runs = repo.list_runs("ws1")
        assert len(runs) >= 3


# ═══════════════════════════════════════════════════════════════
# END-TO-END WITH DB
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_full_deal_with_db(self, session, seed):
        """Full pipeline with DB persistence."""
        pipeline = DealPipeline(session, "ws1", "admin")
        result = pipeline.evaluate(GOOD_DEAL)

        assert result["decision"] in ("GO", "HOLD", "NO_GO")
        assert "stage_results" in result

        # Verify persisted in DB
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(session)
        runs = repo.list_runs("ws1")
        assert len(runs) >= 1
        latest = runs[0]
        assert latest["deal_name"] == "123 Main St Strip Center"

    def test_gaming_deal_with_db(self, session, seed):
        """Gas station + gaming prediction with DB."""
        pipeline = DealPipeline(session, "ws1", "admin")
        result = pipeline.evaluate_with_gaming(
            inputs=GAS_STATION_DEAL.copy(),
            gaming_prediction=GAMING_PREDICTION,
        )
        assert result["decision"] in ("GO", "HOLD", "NO_GO")

    def test_multiple_deals_tracked(self, session, seed):
        """Run several deals and verify audit trail."""
        pipeline = DealPipeline(session, "ws1", "admin")

        deals = [
            {**GOOD_DEAL, "deal_name": "Deal A"},
            {**GOOD_DEAL, "deal_name": "Deal B", "purchase_price": 3000000},
            {**GAS_STATION_DEAL, "deal_name": "Deal C"},
        ]

        results = []
        for d in deals:
            r = pipeline.evaluate(d)
            results.append(r)

        # All should complete
        assert all(r["decision"] in ("GO", "HOLD", "NO_GO") for r in results)

        # Check audit trail
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(session)
        runs = repo.list_runs("ws1")
        assert len(runs) >= 3

    def test_cross_phase_integration(self, session, seed):
        """Phase 4 prediction → Phase 5 contract → Phase 6 deal."""
        # Simulated Phase 4 prediction
        gaming_pred = GAMING_PREDICTION

        # Phase 6 deal with gaming
        pipeline = DealPipeline(session, "ws1", "admin")
        result = pipeline.evaluate_with_gaming(
            inputs={
                **GAS_STATION_DEAL,
                "deal_name": "Cross-Phase Integration Test",
            },
            gaming_prediction=gaming_pred,
        )

        assert result["decision"] in ("GO", "HOLD", "NO_GO")
        # Gaming context should be reflected
        intake_params = result["stage_results"]["intake"]["params"]
        assert intake_params.get("expected_gaming_net_win_monthly") > 0
