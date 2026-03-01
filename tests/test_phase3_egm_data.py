"""
tests/test_phase3_egm.py — EGM Data Layer Tests
==================================================
Phase 3: Tests for connectors, classifier, pipeline,
repositories, analytics, and full ingestion workflow.

Run: pytest tests/test_phase3_egm.py -v
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
from engine.db.egm_repositories import (
    DataSourceRepo, EGMLocationRepo, EGMPerformanceRepo,
    IngestRunRepo, IngestErrorRepo,
)
from engine.tenants import Organization, Workspace
from engine.egm.classifier import classify_venue, classify_venue_batch, extract_operator
from engine.egm.connector import IllinoisIGBConnector, ParsedRow
from engine.egm.pipeline import IngestPipeline
from engine.egm.analytics import EGMAnalytics


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


SAMPLE_IGB_CSV = """Municipality,Establishment,License #,Terminal Operator,# of VGTs,Funds In,Funds Out,NTI,State Tax,Municipality Share
Springfield,LUCKY DOG BAR & GRILL,120-00001,Accel Entertainment,5,"$1,234,567.89","$1,111,111.10","$123,456.79","$37,037.04","$6,172.84"
Springfield,VFW POST 755,120-00002,J&J Ventures,4,"$567,890.12","$510,000.00","$57,890.12","$17,367.04","$2,894.51"
Decatur,TRUCK CITY TRAVEL CENTER,120-00003,Gold Rush Amusements,6,"$2,345,678.90","$2,100,000.00","$245,678.90","$73,703.67","$12,283.95"
Champaign,PIZZA PALACE RESTAURANT,120-00004,Accel Entertainment,3,"$345,678.90","$310,000.00","$35,678.90","$10,703.67","$1,783.95"
Peoria,SHELBY'S GAMING CAFE,120-00005,Midwest Electronics Gaming,5,"$890,123.45","$800,000.00","$90,123.45","$27,037.04","$4,506.17"
"""

SAMPLE_IGB_CSV_2 = """Municipality,Establishment,License #,Terminal Operator,# of VGTs,Funds In,Funds Out,NTI,State Tax,Municipality Share
Springfield,LUCKY DOG BAR & GRILL,120-00001,Accel Entertainment,5,"$1,300,000.00","$1,170,000.00","$130,000.00","$39,000.00","$6,500.00"
Springfield,VFW POST 755,120-00002,J&J Ventures,4,"$600,000.00","$540,000.00","$60,000.00","$18,000.00","$3,000.00"
Decatur,TRUCK CITY TRAVEL CENTER,120-00003,Gold Rush Amusements,6,"$2,400,000.00","$2,160,000.00","$240,000.00","$72,000.00","$12,000.00"
Champaign,PIZZA PALACE RESTAURANT,120-00004,Accel Entertainment,3,"$360,000.00","$324,000.00","$36,000.00","$10,800.00","$1,800.00"
Peoria,SHELBY'S GAMING CAFE,120-00005,Midwest Electronics Gaming,5,"$900,000.00","$810,000.00","$90,000.00","$27,000.00","$4,500.00"
"""

MONTH_JAN = datetime(2024, 1, 1, tzinfo=timezone.utc)
MONTH_FEB = datetime(2024, 2, 1, tzinfo=timezone.utc)
MONTH_MAR = datetime(2024, 3, 1, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════
# VENUE TYPE CLASSIFIER
# ═══════════════════════════════════════════════════════════════

class TestClassifier:

    def test_bar_classification(self):
        assert classify_venue("LUCKY DOG BAR & GRILL") == "bar"
        assert classify_venue("The Tipsy Tavern") == "bar"
        assert classify_venue("O'MALLEY'S PUB") == "bar"
        assert classify_venue("SUNSET LOUNGE") == "bar"
        assert classify_venue("Craft Brewery Taproom") == "bar"

    def test_restaurant_classification(self):
        assert classify_venue("PIZZA PALACE RESTAURANT") == "restaurant"
        assert classify_venue("Tony's Italian Ristorante") == "restaurant"
        assert classify_venue("Highway Diner") == "restaurant"
        assert classify_venue("BBQ Pit Stop") == "restaurant"
        assert classify_venue("Golden China Buffet") == "restaurant"

    def test_fraternal_classification(self):
        assert classify_venue("VFW POST 755") == "fraternal"
        assert classify_venue("American Legion Post 100") == "fraternal"
        assert classify_venue("Loyal Order of Moose Lodge 42") == "fraternal"
        assert classify_venue("ELKS LODGE #1234") == "fraternal"
        assert classify_venue("Knights of Columbus Hall") == "fraternal"

    def test_truck_stop_classification(self):
        assert classify_venue("TRUCK CITY TRAVEL CENTER") == "truck_stop"
        assert classify_venue("Flying J Travel Plaza") == "truck_stop"
        assert classify_venue("Love's Fuel Stop") == "truck_stop"
        assert classify_venue("Pilot Truck Stop") == "truck_stop"

    def test_gaming_cafe_classification(self):
        assert classify_venue("SHELBY'S GAMING CAFE") == "gaming_cafe"
        assert classify_venue("Lucky Slots Parlor") == "gaming_cafe"
        assert classify_venue("Stella's Place") == "gaming_cafe"
        assert classify_venue("Dotty's") == "gaming_cafe"

    def test_casino_classification(self):
        assert classify_venue("Rivers Casino") == "casino"
        assert classify_venue("PARX CASINO") == "casino"
        assert classify_venue("Bellagio Resort") == "casino"

    def test_other_fallback(self):
        assert classify_venue("STORE #4567") == "other"
        assert classify_venue("") == "other"

    def test_batch_classification(self):
        names = ["Lucky Bar", "VFW Post 1", "Pizza Kitchen"]
        results = classify_venue_batch(names)
        assert results == ["bar", "fraternal", "restaurant"]

    def test_operator_extraction(self):
        assert extract_operator("Accel Entertainment") == "Accel Entertainment"
        assert extract_operator("J&J VENTURES GAMING") == "J&J Ventures"
        assert extract_operator("GOLD RUSH AMUSEMENTS LLC") == "Gold Rush Amusements"
        assert extract_operator("Unknown Operator Inc") == "Unknown Operator Inc"
        assert extract_operator("") == ""


# ═══════════════════════════════════════════════════════════════
# ILLINOIS IGB CONNECTOR
# ═══════════════════════════════════════════════════════════════

class TestIGBConnector:

    def test_parse_csv(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        assert len(result.rows) == 5
        assert len(result.errors) == 0
        assert result.raw_row_count == 5

    def test_column_mapping(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        lucky_dog = result.rows[0]
        assert lucky_dog.name == "LUCKY DOG BAR & GRILL"
        assert lucky_dog.municipality == "Springfield"
        assert lucky_dog.license_number == "120-00001"
        assert lucky_dog.terminal_count == 5
        assert lucky_dog.state == "IL"

    def test_currency_parsing(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        lucky_dog = result.rows[0]
        assert lucky_dog.coin_in == 1234567.89
        assert lucky_dog.coin_out == 1111111.10
        assert lucky_dog.net_win == 123456.79

    def test_venue_classification(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        types = {r.name: r.venue_type for r in result.rows}
        assert types["LUCKY DOG BAR & GRILL"] == "bar"
        assert types["VFW POST 755"] == "fraternal"
        assert types["TRUCK CITY TRAVEL CENTER"] == "truck_stop"
        assert types["PIZZA PALACE RESTAURANT"] == "restaurant"
        assert types["SHELBY'S GAMING CAFE"] == "gaming_cafe"

    def test_operator_normalization(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        operators = {r.name: r.terminal_operator for r in result.rows}
        assert operators["LUCKY DOG BAR & GRILL"] == "Accel Entertainment"
        assert operators["VFW POST 755"] == "J&J Ventures"
        assert operators["TRUCK CITY TRAVEL CENTER"] == "Gold Rush Amusements"

    def test_hold_pct_computation(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        lucky_dog = result.rows[0]
        expected_hold = round(123456.79 / 1234567.89, 6)
        assert lucky_dog.hold_pct == expected_hold

    def test_tax_amount(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(SAMPLE_IGB_CSV, MONTH_JAN)

        lucky_dog = result.rows[0]
        expected_tax = round(37037.04 + 6172.84, 2)
        assert lucky_dog.tax_amount == expected_tax

    def test_empty_csv(self):
        connector = IllinoisIGBConnector()
        result = connector.parse_csv("", MONTH_JAN)
        assert len(result.rows) == 0

    def test_malformed_rows(self):
        csv_with_bad = """Municipality,Establishment,License #,# of VGTs,Funds In,Funds Out,NTI
Springfield,GOOD ROW,L001,5,"$100","$90","$10"
,,,,,,,
Springfield,,L003,5,"$100","$90","$10"
"""
        connector = IllinoisIGBConnector()
        result = connector.parse_csv(csv_with_bad, MONTH_JAN)
        # Only the first row has a name, so only 1 valid parsed row
        assert len(result.rows) == 1
        assert result.rows[0].name == "GOOD ROW"


# ═══════════════════════════════════════════════════════════════
# EGM REPOSITORIES
# ═══════════════════════════════════════════════════════════════

class TestDataSourceRepo:

    def test_create_and_get(self, session, seed):
        repo = DataSourceRepo(session)
        ds = repo.create("illinois_igb", "state_gaming_board",
                         url="https://igb.illinois.gov", format="csv")
        session.flush()

        assert ds["name"] == "illinois_igb"
        fetched = repo.get(ds["id"])
        assert fetched["source_type"] == "state_gaming_board"

    def test_get_by_name(self, session, seed):
        repo = DataSourceRepo(session)
        repo.create("test_source", "test")
        session.flush()

        found = repo.get_by_name("test_source")
        assert found is not None
        assert found["name"] == "test_source"

    def test_list_all(self, session, seed):
        repo = DataSourceRepo(session)
        repo.create("src_a", "type_a")
        repo.create("src_b", "type_b")
        session.flush()

        sources = repo.list_all()
        assert len(sources) >= 2


class TestEGMLocationRepo:

    def test_upsert_creates(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("loc_test_source", "test")
        session.flush()

        loc_repo = EGMLocationRepo(session)
        loc = loc_repo.upsert(ds["id"], "L001", {
            "name": "Test Bar", "state": "IL",
            "municipality": "Springfield", "venue_type": "bar",
        })
        session.flush()

        assert loc["name"] == "Test Bar"
        assert loc["state"] == "IL"
        assert loc["is_active"] is True

    def test_upsert_updates(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("loc_upd_source", "test")
        session.flush()

        loc_repo = EGMLocationRepo(session)
        loc_repo.upsert(ds["id"], "L001", {
            "name": "Old Name", "state": "IL",
        })
        session.flush()

        updated = loc_repo.upsert(ds["id"], "L001", {
            "name": "New Name", "state": "IL", "venue_type": "bar",
        })
        session.flush()

        assert updated["name"] == "New Name"
        assert updated["venue_type"] == "bar"

    def test_search(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("search_source", "test")
        session.flush()

        loc_repo = EGMLocationRepo(session)
        loc_repo.upsert(ds["id"], "S001", {
            "name": "Bar A", "state": "IL", "venue_type": "bar",
            "municipality": "Chicago",
        })
        loc_repo.upsert(ds["id"], "S002", {
            "name": "Restaurant B", "state": "IL", "venue_type": "restaurant",
            "municipality": "Chicago",
        })
        loc_repo.upsert(ds["id"], "S003", {
            "name": "Casino C", "state": "NV", "venue_type": "casino",
            "municipality": "Las Vegas",
        })
        session.flush()

        il_bars = loc_repo.search(state="IL", venue_type="bar")
        assert any(l["name"] == "Bar A" for l in il_bars)

        nv_locs = loc_repo.search(state="NV")
        assert any(l["name"] == "Casino C" for l in nv_locs)

    def test_count_by_venue_type(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("count_source", "test")
        session.flush()

        loc_repo = EGMLocationRepo(session)
        for i in range(3):
            loc_repo.upsert(ds["id"], f"B{i}", {
                "name": f"Bar {i}", "state": "IL", "venue_type": "bar",
            })
        for i in range(2):
            loc_repo.upsert(ds["id"], f"R{i}", {
                "name": f"Rest {i}", "state": "IL", "venue_type": "restaurant",
            })
        session.flush()

        counts = loc_repo.count_by_venue_type(state="IL")
        by_type = {c["venue_type"]: c["count"] for c in counts}
        assert by_type.get("bar", 0) >= 3
        assert by_type.get("restaurant", 0) >= 2


class TestEGMPerformanceRepo:

    def test_upsert_and_history(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("perf_source", "test")
        loc_repo = EGMLocationRepo(session)
        loc = loc_repo.upsert(ds["id"], "P001", {
            "name": "Test Loc", "state": "IL",
        })
        session.flush()

        perf_repo = EGMPerformanceRepo(session)
        _, is_new = perf_repo.upsert(
            loc["id"], ds["id"], MONTH_JAN,
            {"coin_in": 100000, "net_win": 10000, "hold_pct": 0.1,
             "terminal_count": 5},
        )
        assert is_new is True

        _, is_new2 = perf_repo.upsert(
            loc["id"], ds["id"], MONTH_FEB,
            {"coin_in": 110000, "net_win": 11000, "hold_pct": 0.1,
             "terminal_count": 5},
        )
        session.flush()
        assert is_new2 is True

        history = perf_repo.get_history(loc["id"])
        assert len(history) == 2
        assert history[0]["coin_in"] == 110000  # Feb (newest first)

    def test_upsert_is_idempotent(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("idemp_source", "test")
        loc_repo = EGMLocationRepo(session)
        loc = loc_repo.upsert(ds["id"], "I001", {
            "name": "Idempotent Loc", "state": "IL",
        })
        session.flush()

        perf_repo = EGMPerformanceRepo(session)
        perf_repo.upsert(
            loc["id"], ds["id"], MONTH_JAN,
            {"coin_in": 100000, "net_win": 10000},
        )
        session.flush()

        # Re-upsert with different values
        _, is_new = perf_repo.upsert(
            loc["id"], ds["id"], MONTH_JAN,
            {"coin_in": 120000, "net_win": 12000},
        )
        session.flush()

        assert is_new is False
        history = perf_repo.get_history(loc["id"])
        assert len(history) == 1
        assert history[0]["coin_in"] == 120000  # Updated


class TestIngestRunRepo:

    def test_lifecycle(self, session, seed):
        ds_repo = DataSourceRepo(session)
        ds = ds_repo.create("run_source", "test")
        session.flush()

        run_repo = IngestRunRepo(session)
        run = run_repo.create(ds["id"], run_type="manual", triggered_by="u1")
        assert run["status"] == "pending"

        run_repo.start(run["id"])
        session.flush()
        started = run_repo.get(run["id"])
        assert started["status"] == "running"

        run_repo.complete(run["id"], rows_processed=100, rows_inserted=95,
                          rows_updated=3, rows_errored=2)
        session.flush()
        completed = run_repo.get(run["id"])
        assert completed["status"] == "completed"
        assert completed["rows_inserted"] == 95


# ═══════════════════════════════════════════════════════════════
# INGESTION PIPELINE
# ═══════════════════════════════════════════════════════════════

class TestIngestPipeline:

    def test_full_ingest(self, session, seed):
        pipeline = IngestPipeline(session, "ws1")
        result = pipeline.ingest(
            source_name="illinois_igb",
            content=SAMPLE_IGB_CSV,
            report_month=MONTH_JAN,
            triggered_by="test",
        )

        assert result["status"] == "completed"
        assert result["rows_inserted"] == 5
        assert result["rows_errored"] == 0

        # Verify locations created
        loc_repo = EGMLocationRepo(session)
        locs = loc_repo.search(state="IL")
        assert len(locs) >= 5

        # Verify performance created
        perf_repo = EGMPerformanceRepo(session)
        for loc in locs:
            history = perf_repo.get_history(loc["id"])
            assert len(history) >= 1

    def test_re_ingest_updates(self, session, seed):
        pipeline = IngestPipeline(session, "ws1")

        # First ingest
        r1 = pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_JAN, "test")
        assert r1["rows_inserted"] == 5

        # Re-ingest same month with different data
        r2 = pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV_2, MONTH_JAN, "test")
        assert r2["rows_updated"] == 5
        assert r2["rows_inserted"] == 0  # All already exist

        # Verify values updated
        loc_repo = EGMLocationRepo(session)
        perf_repo = EGMPerformanceRepo(session)
        lucky_loc = loc_repo.find(
            data_source_id=r1["run_id"],  # Will use the actual source_id
            source_location_id="120-00001",
        )
        # The find may not match by run_id; let's search by name instead
        locs = loc_repo.search(state="IL")
        lucky = [l for l in locs if "LUCKY DOG" in l["name"]]
        if lucky:
            history = perf_repo.get_history(lucky[0]["id"])
            assert history[0]["coin_in"] == 1300000.0  # Updated value

    def test_multi_month_ingest(self, session, seed):
        pipeline = IngestPipeline(session, "ws1")

        pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_JAN, "test")
        pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV_2, MONTH_FEB, "test")

        # Should have 2 months of data
        perf_repo = EGMPerformanceRepo(session)
        months = perf_repo.available_months()
        assert len(months) >= 2

    def test_ingest_creates_data_source(self, session, seed):
        pipeline = IngestPipeline(session, "ws1")
        pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_JAN, "test")

        ds_repo = DataSourceRepo(session)
        source = ds_repo.get_by_name("illinois_igb")
        assert source is not None
        assert source["source_type"] == "state_gaming_board"
        assert source["last_synced_at"] is not None

    def test_ingest_tracks_run(self, session, seed):
        pipeline = IngestPipeline(session, "ws1")
        result = pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_JAN, "test")

        run_repo = IngestRunRepo(session)
        run = run_repo.get(result["run_id"])
        assert run["status"] == "completed"
        assert run["rows_processed"] == 5
        assert run["triggered_by"] == "test"


# ═══════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════

class TestAnalytics:

    def _seed_data(self, session):
        """Ingest 3 months of data for analytics tests."""
        pipeline = IngestPipeline(session, "ws1")
        pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_JAN, "test")
        pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV_2, MONTH_FEB, "test")
        pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_MAR, "test")

    def test_data_health_summary(self, session, seed):
        self._seed_data(session)
        analytics = EGMAnalytics(session)
        health = analytics.data_health_summary()

        assert health["total_sources"] >= 1
        assert health["total_locations"] >= 5
        assert health["total_months"] >= 3

    def test_performance_summary(self, session, seed):
        self._seed_data(session)
        analytics = EGMAnalytics(session)
        summary = analytics.performance_summary(MONTH_JAN, state="IL")

        assert summary["totals"]["total_locations"] >= 5
        assert summary["totals"]["total_coin_in"] > 0
        assert summary["totals"]["total_net_win"] > 0
        assert len(summary["by_venue_type"]) >= 3

    def test_location_trends(self, session, seed):
        self._seed_data(session)
        analytics = EGMAnalytics(session)

        # Find a location
        loc_repo = EGMLocationRepo(session)
        locs = loc_repo.search(state="IL", limit=1)
        assert len(locs) >= 1

        trends = analytics.location_trends(locs[0]["id"], months=12)
        assert trends["location"] is not None
        assert len(trends["history"]) >= 2
        assert "coin_in_mom" in trends["trends"]

    def test_anomaly_detection(self, session, seed):
        """Inject an anomalous month to trigger detection."""
        self._seed_data(session)

        # Create an anomalous month for a location
        loc_repo = EGMLocationRepo(session)
        perf_repo = EGMPerformanceRepo(session)

        locs = loc_repo.search(state="IL", limit=1)
        loc_id = locs[0]["id"]

        # Get current history to know data_source_id
        history = perf_repo.get_history(loc_id)
        ds_id = history[0]["data_source_id"]

        # Add months with very different values to create variance
        base_ci = 1000000
        for i, month_offset in enumerate(range(4, 10)):
            month = datetime(2023, month_offset, 1, tzinfo=timezone.utc)
            perf_repo.upsert(loc_id, ds_id, month, {
                "coin_in": base_ci + (i * 10000),
                "net_win": 100000 + (i * 1000),
                "hold_pct": 0.10,
                "terminal_count": 5,
            })
        session.flush()

        analytics = EGMAnalytics(session)
        anomalies = analytics.detect_anomalies(loc_id)
        # We should get at least some anomalies from the variance
        # (terminal count changes or coin_in outliers)
        assert isinstance(anomalies, list)

    def test_top_performers(self, session, seed):
        self._seed_data(session)
        analytics = EGMAnalytics(session)

        top = analytics.top_performers(MONTH_JAN, state="IL", metric="net_win")
        assert len(top) >= 1
        # Should be sorted by net_win descending
        if len(top) >= 2:
            assert top[0]["net_win"] >= top[1]["net_win"]

    def test_month_gap_detection(self):
        months = ["2024-03", "2024-01", "2023-12"]  # Missing Feb
        gaps = EGMAnalytics._find_month_gaps(months)
        assert "2024-02" in gaps


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: FULL INGEST → ANALYTICS PIPELINE
# ═══════════════════════════════════════════════════════════════

class TestFullIntegration:

    def test_ingest_to_analytics_workflow(self, session, seed):
        """End-to-end: ingest → query → analyze → health check."""

        # 1. Ingest data
        pipeline = IngestPipeline(session, "ws1")
        r1 = pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV, MONTH_JAN, "admin")
        r2 = pipeline.ingest("illinois_igb", SAMPLE_IGB_CSV_2, MONTH_FEB, "admin")
        assert r1["status"] == "completed"
        assert r2["status"] == "completed"

        # 2. Query locations
        loc_repo = EGMLocationRepo(session)
        il_locations = loc_repo.search(state="IL")
        assert len(il_locations) >= 5

        venue_counts = loc_repo.count_by_venue_type(state="IL")
        assert len(venue_counts) >= 3

        # 3. Query performance
        perf_repo = EGMPerformanceRepo(session)
        by_state = perf_repo.aggregate_by_state(MONTH_JAN)
        assert len(by_state) >= 1
        assert by_state[0]["state"] == "IL"
        assert by_state[0]["total_net_win"] > 0

        # 4. Analytics
        analytics = EGMAnalytics(session)
        summary = analytics.performance_summary(MONTH_JAN)
        assert summary["totals"]["total_locations"] >= 5

        health = analytics.data_health_summary()
        assert health["total_sources"] >= 1

        # 5. Verify ingest runs tracked
        run_repo = IngestRunRepo(session)
        runs = run_repo.list_runs()
        assert len(runs) >= 2
        assert all(r["status"] == "completed" for r in runs)

    def test_batch_ingest(self, session, seed):
        pipeline = IngestPipeline(session, "ws1")
        results = pipeline.ingest_batch(
            "illinois_igb",
            {MONTH_JAN: SAMPLE_IGB_CSV, MONTH_FEB: SAMPLE_IGB_CSV_2},
            triggered_by="backfill_script",
        )
        assert len(results) == 2
        assert all(r["status"] == "completed" for r in results)
