"""
tests/test_phase7_portfolio.py — Portfolio Brain Tests
=========================================================
Phase 7: Tests for portfolio asset/debt/NOI repos, dashboard
aggregations, Herfindahl concentration, debt maturity ladder,
gaming exposure, new-deal impact analysis, and full workflow.

Run: pytest tests/test_phase7_portfolio.py -v
"""

import os
import pytest

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.db.models import Base
from engine.db.repositories import OrganizationRepo, WorkspaceRepo
from engine.tenants import Organization, Workspace
from engine.db.portfolio_repositories import (
    PortfolioAssetRepo, PortfolioDebtRepo,
    PortfolioNOIRepo, PortfolioEGMExposureRepo,
)
from engine.portfolio.analytics import PortfolioAnalytics, _herfindahl


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


def _seed_portfolio(session):
    """Create a multi-asset portfolio for testing."""
    asset_repo = PortfolioAssetRepo(session)
    debt_repo = PortfolioDebtRepo(session)
    noi_repo = PortfolioNOIRepo(session)
    egm_repo = PortfolioEGMExposureRepo(session)

    # Asset 1: IL retail strip (owned, no gaming)
    a1 = asset_repo.create(
        "ws1", "Main St Strip Center",
        property_type="retail_strip", state="IL",
        municipality="Springfield", acquisition_cost=1500000,
        current_value=1800000, ownership_type="owned",
    )

    # Asset 2: IL gas station (financed, gaming)
    a2 = asset_repo.create(
        "ws1", "BP Gas Station #12",
        property_type="gas_station", state="IL",
        municipality="Decatur", acquisition_cost=800000,
        current_value=900000, ownership_type="financed",
        has_gaming=True, terminal_count=5,
        contract_type="revenue_share",
    )

    # Asset 3: NV QSR (owned, gaming)
    a3 = asset_repo.create(
        "ws1", "Quick Burger Las Vegas",
        property_type="qsr", state="NV",
        municipality="Las Vegas", acquisition_cost=600000,
        current_value=650000, ownership_type="owned",
        has_gaming=True, terminal_count=3,
        contract_type="flat_lease",
    )

    # Asset 4: IL dollar store (financed, no gaming)
    a4 = asset_repo.create(
        "ws1", "Dollar General #445",
        property_type="dollar", state="IL",
        municipality="Peoria", acquisition_cost=400000,
        current_value=420000, ownership_type="financed",
    )

    session.flush()

    # Debt for financed assets
    debt_repo.create(
        a2["id"], "ws1", lender="First Bank",
        original_balance=640000, current_balance=580000,
        annual_rate=0.065, monthly_payment=4500,
        maturity_date="2028-06",
    )
    debt_repo.create(
        a4["id"], "ws1", lender="Community Credit",
        original_balance=320000, current_balance=290000,
        annual_rate=0.072, monthly_payment=3200,
        maturity_date="2030-01",
    )

    # NOI records
    for period in ["2024-10", "2024-11", "2024-12"]:
        noi_repo.upsert(a1["id"], "ws1", period, 10000)
        noi_repo.upsert(a2["id"], "ws1", period, 6000)
        noi_repo.upsert(a3["id"], "ws1", period, 5000)
        noi_repo.upsert(a4["id"], "ws1", period, 3500)

    # EGM exposure
    egm_repo.create(
        a2["id"], "ws1", machine_count=5,
        monthly_net_win=15000, contract_type="revenue_share",
    )
    egm_repo.create(
        a3["id"], "ws1", machine_count=3,
        monthly_net_win=8000, contract_type="flat_lease",
    )

    session.flush()
    return [a1, a2, a3, a4]


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO ASSET REPO
# ═══════════════════════════════════════════════════════════════

class TestPortfolioAssetRepo:

    def test_create_and_get(self, session, seed):
        repo = PortfolioAssetRepo(session)
        asset = repo.create(
            "ws1", "Test Property",
            property_type="retail_strip", state="IL",
            current_value=1000000,
        )
        session.flush()
        assert asset["name"] == "Test Property"

        fetched = repo.get(asset["id"])
        assert fetched is not None
        assert fetched["current_value"] == 1000000

    def test_list_assets(self, session, seed):
        repo = PortfolioAssetRepo(session)
        repo.create("ws1", "A1", current_value=500000)
        repo.create("ws1", "A2", current_value=600000)
        session.flush()

        assets = repo.list_assets("ws1")
        assert len(assets) >= 2

    def test_update_value(self, session, seed):
        repo = PortfolioAssetRepo(session)
        asset = repo.create("ws1", "Val Test", current_value=500000)
        session.flush()

        repo.update_value(asset["id"], 550000)
        session.flush()

        fetched = repo.get(asset["id"])
        assert fetched["current_value"] == 550000

    def test_deactivate(self, session, seed):
        repo = PortfolioAssetRepo(session)
        asset = repo.create("ws1", "Deact Test")
        session.flush()

        repo.deactivate(asset["id"])
        session.flush()

        fetched = repo.get(asset["id"])
        assert fetched["is_active"] is False

    def test_count_by_state(self, session, seed):
        _seed_portfolio(session)
        repo = PortfolioAssetRepo(session)
        by_state = repo.count_by_state("ws1")
        assert len(by_state) >= 2  # IL and NV

    def test_count_by_property_type(self, session, seed):
        _seed_portfolio(session)
        repo = PortfolioAssetRepo(session)
        by_type = repo.count_by_property_type("ws1")
        assert len(by_type) >= 3


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO DEBT REPO
# ═══════════════════════════════════════════════════════════════

class TestPortfolioDebtRepo:

    def test_create_and_list(self, session, seed):
        asset_repo = PortfolioAssetRepo(session)
        a = asset_repo.create("ws1", "Debt Test", current_value=500000)
        session.flush()

        debt_repo = PortfolioDebtRepo(session)
        debt_repo.create(
            a["id"], "ws1", lender="Bank A",
            current_balance=300000, maturity_date="2029-01",
        )
        session.flush()

        debts = debt_repo.list_for_asset(a["id"])
        assert len(debts) == 1
        assert debts[0]["lender"] == "Bank A"

    def test_total_debt(self, session, seed):
        _seed_portfolio(session)
        repo = PortfolioDebtRepo(session)
        total = repo.total_debt("ws1")
        assert total > 0  # 580000 + 290000 = 870000

    def test_maturity_ladder(self, session, seed):
        _seed_portfolio(session)
        repo = PortfolioDebtRepo(session)
        ladder = repo.maturity_ladder("ws1")
        assert len(ladder) >= 1
        assert all("year" in r and "maturing_balance" in r for r in ladder)


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO NOI REPO
# ═══════════════════════════════════════════════════════════════

class TestPortfolioNOIRepo:

    def test_upsert_and_history(self, session, seed):
        asset_repo = PortfolioAssetRepo(session)
        a = asset_repo.create("ws1", "NOI Test")
        session.flush()

        noi_repo = PortfolioNOIRepo(session)
        noi_repo.upsert(a["id"], "ws1", "2024-01", 10000)
        noi_repo.upsert(a["id"], "ws1", "2024-02", 11000)
        session.flush()

        history = noi_repo.get_history(a["id"])
        assert len(history) == 2

    def test_upsert_updates_existing(self, session, seed):
        asset_repo = PortfolioAssetRepo(session)
        a = asset_repo.create("ws1", "Upsert Test")
        session.flush()

        noi_repo = PortfolioNOIRepo(session)
        r1 = noi_repo.upsert(a["id"], "ws1", "2024-01", 10000)
        assert r1["updated"] is False
        r2 = noi_repo.upsert(a["id"], "ws1", "2024-01", 12000)
        assert r2["updated"] is True
        assert r2["noi_amount"] == 12000

    def test_total_noi(self, session, seed):
        _seed_portfolio(session)
        noi_repo = PortfolioNOIRepo(session)
        total = noi_repo.total_noi("ws1", "2024-12")
        assert total > 0


# ═══════════════════════════════════════════════════════════════
# EGM EXPOSURE REPO
# ═══════════════════════════════════════════════════════════════

class TestEGMExposureRepo:

    def test_create_and_list(self, session, seed):
        asset_repo = PortfolioAssetRepo(session)
        a = asset_repo.create("ws1", "EGM Test", has_gaming=True)
        session.flush()

        egm_repo = PortfolioEGMExposureRepo(session)
        egm_repo.create(a["id"], "ws1", machine_count=5, monthly_net_win=15000)
        session.flush()

        exposures = egm_repo.list_for_workspace("ws1")
        assert len(exposures) >= 1

    def test_total_gaming_exposure(self, session, seed):
        _seed_portfolio(session)
        repo = PortfolioEGMExposureRepo(session)
        total = repo.total_gaming_exposure("ws1")
        assert total["total_machines"] == 8  # 5 + 3
        assert total["total_monthly_net_win"] == 23000  # 15000 + 8000
        assert total["gaming_locations"] == 2


# ═══════════════════════════════════════════════════════════════
# HERFINDAHL INDEX
# ═══════════════════════════════════════════════════════════════

class TestHerfindahl:

    def test_perfectly_diversified(self):
        # 4 equal assets → HHI = 4 * (0.25)^2 = 0.25
        hhi = _herfindahl([250, 250, 250, 250], 1000)
        assert abs(hhi - 0.25) < 0.001

    def test_fully_concentrated(self):
        # 1 asset → HHI = 1.0
        hhi = _herfindahl([1000], 1000)
        assert abs(hhi - 1.0) < 0.001

    def test_empty_portfolio(self):
        assert _herfindahl([], 0) == 0.0

    def test_moderate_concentration(self):
        # One big, three small
        hhi = _herfindahl([700, 100, 100, 100], 1000)
        assert hhi > 0.25  # Concentrated


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO DASHBOARD
# ═══════════════════════════════════════════════════════════════

class TestDashboard:

    def test_dashboard_summary(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()

        summary = dash["summary"]
        assert summary["total_assets"] == 4
        assert summary["total_value"] > 0
        assert summary["total_debt"] > 0
        assert summary["leverage_ratio"] > 0
        assert summary["leverage_ratio"] < 1.0

    def test_dashboard_by_state(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()

        by_state = dash["by_state"]
        states = {s["state"] for s in by_state}
        assert "IL" in states
        assert "NV" in states

    def test_dashboard_ownership_split(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()

        split = dash["ownership_split"]
        assert split["owned"] == 2  # strip center + QSR
        assert split["financed"] == 2  # gas station + dollar

    def test_dashboard_gaming_exposure(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()

        gaming = dash["gaming_exposure"]
        assert gaming["total_machines"] == 8
        assert gaming["total_monthly_net_win"] == 23000
        assert gaming["gaming_locations"] == 2

    def test_dashboard_debt_maturity(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()

        ladder = dash["debt_maturity_ladder"]
        assert len(ladder) >= 1

    def test_dashboard_concentration(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()

        conc = dash["concentration"]
        assert "state_hhi" in conc
        assert "property_type_hhi" in conc
        # IL-heavy portfolio should show some concentration
        assert conc["state_hhi"] > 0


# ═══════════════════════════════════════════════════════════════
# NEW DEAL IMPACT
# ═══════════════════════════════════════════════════════════════

class TestNewDealImpact:

    def test_basic_impact(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")

        impact = analytics.new_deal_impact({
            "name": "New IL Gas Station",
            "state": "IL",
            "property_type": "gas_station",
            "current_value": 700000,
            "debt_amount": 500000,
            "has_gaming": True,
        })

        assert impact["deal_name"] == "New IL Gas Station"
        assert impact["portfolio_after"]["total_value"] > impact["portfolio_before"]["total_value"]
        assert impact["portfolio_after"]["asset_count"] == impact["portfolio_before"]["asset_count"] + 1
        assert impact["deltas"]["value_added"] == 700000

    def test_high_concentration_warns(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")

        # Add a huge IL deal → should warn about concentration
        impact = analytics.new_deal_impact({
            "name": "Mega IL Mall",
            "state": "IL",
            "current_value": 10000000,
            "debt_amount": 8000000,
        })

        # Should have warnings about concentration and/or leverage
        assert len(impact["warnings"]) > 0
        assert impact["recommendation"] == "CAUTION"

    def test_diversifying_deal_no_warning(self, session, seed):
        _seed_portfolio(session)
        analytics = PortfolioAnalytics(session, "ws1")

        # Small CO deal → diversifies state exposure
        impact = analytics.new_deal_impact({
            "name": "CO Strip Center",
            "state": "CO",
            "current_value": 500000,
            "debt_amount": 0,
        })

        # Should be OK (diversifying)
        assert impact["recommendation"] == "OK"

    def test_empty_portfolio_impact(self, session, seed):
        analytics = PortfolioAnalytics(session, "ws1")
        impact = analytics.new_deal_impact({
            "name": "First Deal",
            "state": "IL",
            "current_value": 1000000,
        })
        assert impact["portfolio_before"]["asset_count"] == 0
        assert impact["portfolio_after"]["asset_count"] == 1


# ═══════════════════════════════════════════════════════════════
# END-TO-END
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_full_portfolio_workflow(self, session, seed):
        """Build portfolio → dashboard → new deal impact → verify."""
        assets = _seed_portfolio(session)

        # 1. Dashboard
        analytics = PortfolioAnalytics(session, "ws1")
        dash = analytics.dashboard()
        assert dash["summary"]["total_assets"] == 4
        assert dash["summary"]["total_value"] > 0
        assert dash["gaming_exposure"]["total_machines"] == 8

        # 2. New deal impact
        impact = analytics.new_deal_impact({
            "name": "New PA Dollar Store",
            "state": "PA",
            "property_type": "dollar",
            "current_value": 350000,
        })
        assert impact["portfolio_after"]["asset_count"] == 5
        # PA diversifies → HHI should decrease
        assert impact["deltas"]["hhi_change"] <= 0

        # 3. Add the asset
        repo = PortfolioAssetRepo(session)
        repo.create(
            "ws1", "New PA Dollar Store",
            property_type="dollar", state="PA",
            current_value=350000,
        )
        session.flush()

        # 4. Dashboard should update
        dash2 = analytics.dashboard()
        assert dash2["summary"]["total_assets"] == 5

        # 5. Verify state diversification
        states = {s["state"] for s in dash2["by_state"]}
        assert "PA" in states
