"""
tests/test_phase4_forecaster.py — EGM Forecaster Tests
=========================================================
Phase 4: Tests for feature engineering, quantile model,
confidence scoring, prediction service, model registry,
and end-to-end train→predict→audit workflow.

Run: pytest tests/test_phase4_forecaster.py -v
"""

import os
import json
import math
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
)
from engine.db.forecast_repositories import ModelRegistryRepo, PredictionLogRepo
from engine.tenants import Organization, Workspace
from engine.egm.forecaster import (
    QuantileModel, QuantileParams, compute_confidence,
    find_similar_locations, _quantile, _std,
)
from engine.egm.features import (
    FeatureEngineer, SEASONAL_INDEX, _encode_venue_type, _compute_slope,
)
from engine.egm.prediction import PredictionService
from engine.egm.pipeline import IngestPipeline


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


def _make_training_data(n_per_type=50):
    """Generate synthetic training data."""
    import random
    random.seed(42)
    data = []
    venue_configs = {
        "bar": {"ci_mean": 80000, "ci_std": 30000, "hp_mean": 0.26, "hp_std": 0.04},
        "restaurant": {"ci_mean": 50000, "ci_std": 20000, "hp_mean": 0.24, "hp_std": 0.03},
        "fraternal": {"ci_mean": 40000, "ci_std": 15000, "hp_mean": 0.25, "hp_std": 0.03},
        "truck_stop": {"ci_mean": 150000, "ci_std": 50000, "hp_mean": 0.28, "hp_std": 0.05},
        "gaming_cafe": {"ci_mean": 120000, "ci_std": 40000, "hp_mean": 0.27, "hp_std": 0.04},
    }
    for vt, cfg in venue_configs.items():
        for i in range(n_per_type):
            tc = random.randint(3, 6)
            ci = max(1000, random.gauss(cfg["ci_mean"], cfg["ci_std"])) * tc / 5
            hp = max(0.05, min(0.50, random.gauss(cfg["hp_mean"], cfg["hp_std"])))
            nw = ci * hp
            month = random.randint(1, 12)
            data.append({
                "venue_type": vt,
                "state": "IL",
                "terminal_count": tc,
                "coin_in": round(ci, 2),
                "coin_out": round(ci - nw, 2),
                "net_win": round(nw, 2),
                "hold_pct": round(hp, 6),
                "report_month": f"2024-{month:02d}",
            })
    return data


TRAINING_DATA = _make_training_data(50)

SAMPLE_CSV = """Municipality,Establishment,License #,Terminal Operator,# of VGTs,Funds In,Funds Out,NTI,State Tax,Municipality Share
Springfield,LUCKY DOG BAR & GRILL,120-00001,Accel,5,"$1,000,000","$900,000","$100,000","$30,000","$5,000"
Springfield,VFW POST 755,120-00002,J&J,4,"$500,000","$450,000","$50,000","$15,000","$2,500"
Decatur,TRUCK CITY TRAVEL CENTER,120-00003,Gold Rush,6,"$2,000,000","$1,800,000","$200,000","$60,000","$10,000"
Champaign,PIZZA PALACE RESTAURANT,120-00004,Accel,3,"$300,000","$270,000","$30,000","$9,000","$1,500"
Peoria,SHELBY'S GAMING CAFE,120-00005,Midwest,5,"$800,000","$720,000","$80,000","$24,000","$4,000"
"""

MONTH_JAN = datetime(2024, 1, 1, tzinfo=timezone.utc)
MONTH_FEB = datetime(2024, 2, 1, tzinfo=timezone.utc)


def _seed_egm_data(session, seed):
    """Ingest sample data for DB-dependent tests."""
    pipeline = IngestPipeline(session, "ws1")
    pipeline.ingest("illinois_igb", SAMPLE_CSV, MONTH_JAN, "test")
    pipeline.ingest("illinois_igb", SAMPLE_CSV, MONTH_FEB, "test")
    session.flush()


# ═══════════════════════════════════════════════════════════════
# QUANTILE MODEL — TRAINING
# ═══════════════════════════════════════════════════════════════

class TestQuantileModelTraining:

    def test_train_from_data(self):
        model = QuantileModel()
        assert not model.is_trained
        metrics = model.train(TRAINING_DATA)
        assert model.is_trained
        assert metrics["training_samples"] == len(TRAINING_DATA)
        assert metrics["venue_types"] == 5
        assert metrics["coin_in_global_p50"] > 0
        assert metrics["hold_pct_global_p50"] > 0

    def test_train_stores_group_stats(self):
        model = QuantileModel()
        model.train(TRAINING_DATA)
        params = model.to_params()

        ci_groups = params["coin_in"]["groups"]
        assert "bar" in ci_groups
        assert "truck_stop" in ci_groups

        bar_stats = ci_groups["bar"]
        assert bar_stats["count"] > 0
        assert bar_stats["p10"] < bar_stats["p50"] < bar_stats["p90"]
        assert bar_stats["per_terminal"]["p50"] > 0

    def test_train_computes_seasonal(self):
        model = QuantileModel()
        model.train(TRAINING_DATA)
        params = model.to_params()
        seasonal = params["coin_in"]["seasonal_adj"]
        # Should have 12 months
        assert len(seasonal) == 12

    def test_train_empty_data(self):
        model = QuantileModel()
        result = model.train([])
        assert "error" in result

    def test_serialization_roundtrip(self):
        model = QuantileModel()
        model.train(TRAINING_DATA)
        params = model.to_params()

        # Verify JSON serializable
        json_str = json.dumps(params)
        params_back = json.loads(json_str)

        model2 = QuantileModel(params_back)
        assert model2.is_trained

        # Predictions should match
        features = {
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        }
        pred1 = model.predict(features)
        pred2 = model2.predict(features)
        assert pred1["coin_in"]["p50"] == pred2["coin_in"]["p50"]


# ═══════════════════════════════════════════════════════════════
# QUANTILE MODEL — PREDICTION
# ═══════════════════════════════════════════════════════════════

class TestQuantileModelPrediction:

    @pytest.fixture
    def trained_model(self):
        model = QuantileModel()
        model.train(TRAINING_DATA)
        return model

    def test_predict_bar(self, trained_model):
        pred = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        assert pred["coin_in"]["p10"] > 0
        assert pred["coin_in"]["p10"] < pred["coin_in"]["p50"]
        assert pred["coin_in"]["p50"] < pred["coin_in"]["p90"]
        assert pred["hold_pct"]["p50"] > 0
        assert pred["net_win"]["p50"] > 0

    def test_predict_truck_stop_higher_than_bar(self, trained_model):
        bar = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        truck = trained_model.predict({
            "venue_type": "truck_stop", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        # Truck stops should have higher coin_in
        assert truck["coin_in"]["p50"] > bar["coin_in"]["p50"]

    def test_more_terminals_increases_prediction(self, trained_model):
        pred3 = trained_model.predict({
            "venue_type": "bar", "terminal_count": 3,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        pred6 = trained_model.predict({
            "venue_type": "bar", "terminal_count": 6,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        assert pred6["coin_in"]["p50"] > pred3["coin_in"]["p50"]

    def test_seasonal_adjustment(self, trained_model):
        summer = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.08, "market_maturity": 1.0,
            "has_history": 0,
        })
        winter = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 0.88, "market_maturity": 1.0,
            "has_history": 0,
        })
        assert summer["coin_in"]["p50"] > winter["coin_in"]["p50"]

    def test_history_blending(self, trained_model):
        """Historical data should influence prediction."""
        no_history = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        with_history = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 1,
            "trailing_avg_coin_in": 200000,  # Very high
            "trailing_avg_hold_pct": 0.30,
        })
        # History blending should pull prediction up
        assert with_history["coin_in"]["p50"] > no_history["coin_in"]["p50"]

    def test_net_win_is_product(self, trained_model):
        pred = trained_model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        expected_nw_p50 = round(
            pred["coin_in"]["p50"] * pred["hold_pct"]["p50"], 2
        )
        assert pred["net_win"]["p50"] == expected_nw_p50

    def test_untrained_returns_zeros(self):
        model = QuantileModel()
        pred = model.predict({
            "venue_type": "bar", "terminal_count": 5,
        })
        assert pred["coin_in"]["p50"] == 0

    def test_unknown_venue_type_uses_global(self, trained_model):
        pred = trained_model.predict({
            "venue_type": "exotic_unknown_type", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        # Should still produce a prediction from global stats
        assert pred["coin_in"]["p50"] > 0


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════

class TestConfidence:

    def test_high_confidence(self):
        model = QuantileModel()
        model.train(TRAINING_DATA)

        features = {
            "venue_type": "bar",
            "feature_completeness": 0.9,
            "has_history": 1,
            "months_of_data": 24,
        }
        predictions = model.predict({
            "venue_type": "bar", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 1, "trailing_avg_coin_in": 80000,
            "trailing_avg_hold_pct": 0.26,
        })
        score, level = compute_confidence(features, predictions, model._coin_in)
        assert score > 0.5
        assert level in ("MEDIUM", "HIGH")

    def test_low_confidence_no_history(self):
        model = QuantileModel()
        model.train(TRAINING_DATA)

        features = {
            "venue_type": "other",
            "feature_completeness": 0.3,
            "has_history": 0,
            "months_of_data": 0,
        }
        predictions = model.predict({
            "venue_type": "other", "terminal_count": 5,
            "seasonal_index": 1.0, "market_maturity": 1.0,
            "has_history": 0,
        })
        score, level = compute_confidence(features, predictions, model._coin_in)
        assert score < 0.7

    def test_confidence_bounded(self):
        score, level = compute_confidence(
            {"feature_completeness": 1.0, "has_history": 1, "months_of_data": 100,
             "venue_type": "bar"},
            {"coin_in": {"p10": 50000, "p50": 80000, "p90": 110000}},
            QuantileParams(groups={"bar": {"count": 10000}}),
        )
        assert 0.0 <= score <= 1.0
        assert level in ("LOW", "MEDIUM", "HIGH")


# ═══════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════

class TestFeatureEngineering:

    def test_encode_venue_type(self):
        assert _encode_venue_type("bar") == 1
        assert _encode_venue_type("restaurant") == 2
        assert _encode_venue_type("unknown") == 0

    def test_seasonal_index_coverage(self):
        assert len(SEASONAL_INDEX) == 12
        assert all(0.5 < v < 1.5 for v in SEASONAL_INDEX.values())

    def test_compute_slope_flat(self):
        # Newest first, all same → slope ≈ 0
        assert _compute_slope([100, 100, 100, 100]) == 0.0

    def test_compute_slope_increasing(self):
        # Newest first: 140, 130, 120, 110, 100
        slope = _compute_slope([140, 130, 120, 110, 100])
        assert slope > 0  # Increasing over time

    def test_compute_features_with_db(self, session, seed):
        _seed_egm_data(session, seed)
        fe = FeatureEngineer(session)
        features = fe.compute_features(
            venue_type="bar", state="IL",
            terminal_count=5, municipality="Springfield",
        )
        assert "venue_type_encoded" in features
        assert "seasonal_index" in features
        assert "state_location_count" in features
        assert "feature_completeness" in features
        assert features["venue_type"] == "bar"
        assert features["terminal_count"] == 5

    def test_features_for_existing_location(self, session, seed):
        _seed_egm_data(session, seed)
        loc_repo = EGMLocationRepo(session)
        locs = loc_repo.search(state="IL", limit=1)
        assert len(locs) >= 1

        fe = FeatureEngineer(session)
        features = fe.compute_features(
            venue_type=locs[0]["venue_type"], state="IL",
            terminal_count=5, location_id=locs[0]["id"],
        )
        assert features["has_history"] == 1
        assert features["trailing_avg_coin_in"] > 0


# ═══════════════════════════════════════════════════════════════
# MODEL REGISTRY REPO
# ═══════════════════════════════════════════════════════════════

class TestModelRegistry:

    def test_register_and_retrieve(self, session, seed):
        repo = ModelRegistryRepo(session)
        model = repo.register(
            model_name="test_model", model_type="quantile",
            metrics={"mae": 5000}, parameters={"groups": {}},
            training_data_range="2024-01 to 2024-12",
        )
        session.flush()

        assert model["version"] == 1
        assert model["is_champion"] is False

        fetched = repo.get_version("test_model", 1)
        assert fetched is not None
        assert fetched["metrics"]["mae"] == 5000

    def test_auto_version_increment(self, session, seed):
        repo = ModelRegistryRepo(session)
        v1 = repo.register("auto_v", "quantile", {"v": 1}, {})
        v2 = repo.register("auto_v", "quantile", {"v": 2}, {})
        session.flush()

        assert v1["version"] == 1
        assert v2["version"] == 2

    def test_promote_champion(self, session, seed):
        repo = ModelRegistryRepo(session)
        repo.register("promote_test", "quantile", {}, {})
        repo.register("promote_test", "quantile", {}, {})
        session.flush()

        repo.promote("promote_test", 2, "admin")
        session.flush()

        champion = repo.get_champion("promote_test")
        assert champion is not None
        assert champion["version"] == 2
        assert champion["is_champion"] is True
        assert champion["promoted_by"] == "admin"

    def test_promote_demotes_previous(self, session, seed):
        repo = ModelRegistryRepo(session)
        repo.register("demote_test", "quantile", {}, {})
        repo.register("demote_test", "quantile", {}, {})
        session.flush()

        repo.promote("demote_test", 1, "admin")
        session.flush()
        repo.promote("demote_test", 2, "admin")
        session.flush()

        v1 = repo.get_version("demote_test", 1)
        assert v1["is_champion"] is False

        champion = repo.get_champion("demote_test")
        assert champion["version"] == 2

    def test_list_versions(self, session, seed):
        repo = ModelRegistryRepo(session)
        for i in range(3):
            repo.register("list_test", "quantile", {"v": i}, {})
        session.flush()

        versions = repo.list_versions("list_test")
        assert len(versions) == 3
        assert versions[0]["version"] == 3  # newest first


# ═══════════════════════════════════════════════════════════════
# PREDICTION LOG REPO
# ═══════════════════════════════════════════════════════════════

class TestPredictionLog:

    def test_log_and_retrieve(self, session, seed):
        repo = PredictionLogRepo(session)
        pred_id = repo.log(
            workspace_id="ws1", model_name="test",
            model_version=1, inputs={"venue": "bar"},
            features={"tc": 5}, predictions={"coin_in": 80000},
            confidence=0.75, execution_ms=10, user_id="u1",
        )
        session.flush()

        fetched = repo.get(pred_id)
        assert fetched is not None
        assert fetched["confidence"] == 0.75
        assert fetched["inputs"]["venue"] == "bar"

    def test_list_recent(self, session, seed):
        repo = PredictionLogRepo(session)
        for i in range(5):
            repo.log("ws1", "test", 1, {}, {}, {"v": i}, 0.5)
        session.flush()

        recent = repo.list_recent("ws1", limit=3)
        assert len(recent) == 3
        assert recent[0]["predictions"]["v"] == 4  # newest first


# ═══════════════════════════════════════════════════════════════
# PREDICTION SERVICE — FULL INTEGRATION
# ═══════════════════════════════════════════════════════════════

class TestPredictionService:

    def test_train_then_predict_in_memory(self):
        """Pure in-memory: train → predict, no DB."""
        model = QuantileModel()
        model.train(TRAINING_DATA)

        svc = PredictionService(model=model)
        result = svc.predict(
            venue_type="bar", state="IL", terminal_count=5,
            include_similar=False,
        )
        assert result["coin_in"]["p50"] > 0
        assert result["hold_pct"]["p50"] > 0
        assert result["net_win"]["p50"] > 0
        assert result["confidence"] > 0
        assert result["confidence_level"] in ("LOW", "MEDIUM", "HIGH")

    def test_train_from_db(self, session, seed):
        """Train from ingested EGM data."""
        _seed_egm_data(session, seed)

        svc = PredictionService(session, "ws1", "admin")
        result = svc.train_model("egm_forecaster")

        assert result.get("error") is None
        assert result["training_samples"] > 0
        assert result["version"] >= 1
        assert result["is_champion"] is True

    def test_predict_with_db_model(self, session, seed):
        """Train → predict using DB-backed model registry."""
        _seed_egm_data(session, seed)

        svc = PredictionService(session, "ws1", "admin")
        svc.train_model("egm_forecaster")
        session.flush()

        # New service instance — should load champion from registry
        svc2 = PredictionService(session, "ws1", "user1")
        result = svc2.predict(
            venue_type="bar", state="IL", terminal_count=5,
            municipality="Springfield",
        )

        assert result["coin_in"]["p50"] > 0
        assert "prediction_id" in result
        assert result["confidence"] > 0

    def test_predict_logs_to_audit(self, session, seed):
        _seed_egm_data(session, seed)

        svc = PredictionService(session, "ws1", "admin")
        svc.train_model("egm_forecaster")
        session.flush()

        svc.predict(
            venue_type="restaurant", state="IL", terminal_count=3,
        )
        session.flush()

        repo = PredictionLogRepo(session)
        logs = repo.list_recent("ws1")
        assert len(logs) >= 1
        assert logs[0]["inputs"]["venue_type"] == "restaurant"

    def test_no_model_returns_error(self, session, seed):
        svc = PredictionService(session, "ws1")
        result = svc.predict(venue_type="bar", state="IL")
        assert "error" in result

    def test_model_info(self, session, seed):
        _seed_egm_data(session, seed)

        svc = PredictionService(session, "ws1", "admin")
        svc.train_model("egm_forecaster")
        session.flush()

        info = svc.get_model_info("egm_forecaster")
        assert info["champion"] is not None
        assert info["total_versions"] >= 1


# ═══════════════════════════════════════════════════════════════
# MATH HELPERS
# ═══════════════════════════════════════════════════════════════

class TestMathHelpers:

    def test_quantile(self):
        vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        assert _quantile(vals, 0.5) == 55.0
        assert _quantile(vals, 0.0) == 10.0
        assert _quantile(vals, 1.0) == 100.0
        assert _quantile(vals, 0.1) == 19.0

    def test_quantile_single(self):
        assert _quantile([42], 0.5) == 42.0

    def test_quantile_empty(self):
        assert _quantile([], 0.5) == 0.0

    def test_std(self):
        vals = [10, 10, 10, 10]
        assert _std(vals) == 0.0
        vals2 = [0, 10]
        assert _std(vals2) == 5.0


# ═══════════════════════════════════════════════════════════════
# END-TO-END: INGEST → TRAIN → PREDICT → AUDIT
# ═══════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_full_workflow(self, session, seed):
        """Complete Phase 3+4 pipeline: ingest data → train → predict → verify."""

        # 1. Ingest data (Phase 3)
        pipeline = IngestPipeline(session, "ws1")
        r1 = pipeline.ingest("illinois_igb", SAMPLE_CSV, MONTH_JAN, "admin")
        r2 = pipeline.ingest("illinois_igb", SAMPLE_CSV, MONTH_FEB, "admin")
        assert r1["status"] == "completed"
        assert r2["status"] == "completed"

        # 2. Train model (Phase 4)
        svc = PredictionService(session, "ws1", "admin")
        train_result = svc.train_model("egm_forecaster")
        assert train_result["training_samples"] > 0
        assert train_result["is_champion"] is True
        session.flush()

        # 3. Predict for a new location
        prediction = svc.predict(
            venue_type="bar", state="IL", terminal_count=5,
            municipality="Springfield", include_similar=True,
        )
        assert prediction["coin_in"]["p50"] > 0
        assert prediction["hold_pct"]["p50"] > 0
        assert prediction["net_win"]["p50"] > 0
        assert prediction["confidence"] > 0
        assert "prediction_id" in prediction
        session.flush()

        # 4. Predict for an existing location
        loc_repo = EGMLocationRepo(session)
        locs = loc_repo.search(state="IL", venue_type="bar", limit=1)
        if locs:
            existing_pred = svc.predict(
                venue_type="bar", state="IL", terminal_count=5,
                location_id=locs[0]["id"],
            )
            # Should have historical features blended in
            assert existing_pred["coin_in"]["p50"] > 0

        # 5. Verify model registry
        reg = ModelRegistryRepo(session)
        champion = reg.get_champion("egm_forecaster")
        assert champion is not None
        assert champion["is_champion"] is True
        assert champion["metrics"]["training_samples"] > 0

        # 6. Verify prediction log
        pred_log = PredictionLogRepo(session)
        logs = pred_log.list_recent("ws1")
        assert len(logs) >= 1
        assert logs[0]["model_name"] == "egm_forecaster"

        # 7. Train a second version
        train2 = svc.train_model("egm_forecaster")
        assert train2["version"] == 2
        session.flush()

        versions = reg.list_versions("egm_forecaster")
        assert len(versions) == 2
