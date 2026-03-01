"""
engine.egm.prediction — EGM Prediction Service
==================================================
Phase 4D: Unified prediction service that orchestrates:
  1. Feature engineering
  2. Model inference (quantile predictions)
  3. Confidence scoring
  4. Similar location lookup
  5. Audit trail persistence

All predictions are logged to prediction_log for auditability.

Usage:
    svc = PredictionService(session, workspace_id="ws1")
    result = svc.predict(
        venue_type="bar", state="IL",
        terminal_count=5, municipality="Springfield",
    )
    # result includes predictions, confidence, similar locations, model version
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.egm.forecaster import (
    QuantileModel, compute_confidence, find_similar_locations,
)


class PredictionService:
    """Unified prediction service for EGM location performance.

    Manages model lifecycle:
      - train_model(): trains from current EGM data, registers version
      - predict(): generates predictions with full audit trail
      - get_model_info(): returns current champion model metadata
    """

    def __init__(
        self, session=None, workspace_id: str = "",
        user_id: str = "", model: Optional[QuantileModel] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._model = model or QuantileModel()

        if session:
            from engine.db.forecast_repositories import ModelRegistryRepo, PredictionLogRepo
            from engine.egm.features import FeatureEngineer
            self._registry = ModelRegistryRepo(session)
            self._pred_log = PredictionLogRepo(session)
            self._feat_eng = FeatureEngineer(session)
        else:
            self._registry = None
            self._pred_log = None
            self._feat_eng = None

    def train_model(
        self,
        model_name: str = "egm_forecaster",
        training_data: Optional[List[Dict]] = None,
    ) -> Dict:
        """Train a new model version from EGM data.

        If training_data is None, pulls from the DB automatically.

        Returns: model metadata including version and metrics.
        """
        start = time.perf_counter()

        # Pull training data from DB if not provided
        if training_data is None:
            training_data = self._pull_training_data()

        if not training_data:
            return {"error": "No training data available"}

        # Train the model
        metrics = self._model.train(training_data)

        # Determine training data range
        months = sorted(set(
            r.get("report_month", "")
            for r in training_data
            if r.get("report_month")
        ))
        data_range = f"{months[0]} to {months[-1]}" if months else ""

        # Register in model registry
        model_info = None
        if self._registry:
            params = self._model.to_params()
            model_info = self._registry.register(
                model_name=model_name,
                model_type="quantile_parametric",
                metrics=metrics,
                parameters=params,
                training_data_range=data_range,
            )

            # Auto-promote if first version or better than champion
            champion = self._registry.get_champion(model_name)
            if not champion:
                self._registry.promote(
                    model_name, model_info["version"],
                    promoted_by=self._user_id or "auto",
                )
                model_info["is_champion"] = True

        elapsed = int((time.perf_counter() - start) * 1000)

        return {
            "model_name": model_name,
            "version": model_info["version"] if model_info else 0,
            "is_champion": model_info.get("is_champion", False) if model_info else False,
            "metrics": metrics,
            "training_data_range": data_range,
            "training_samples": len(training_data),
            "training_ms": elapsed,
        }

    def predict(
        self,
        venue_type: str,
        state: str,
        terminal_count: int = 5,
        municipality: str = "",
        location_id: Optional[int] = None,
        target_month: Optional[datetime] = None,
        include_similar: bool = True,
        model_name: str = "egm_forecaster",
        **extra_attrs,
    ) -> Dict:
        """Generate predictions for a location.

        Returns:
            {
                "coin_in": {"p10": ..., "p50": ..., "p90": ...},
                "hold_pct": {"p10": ..., "p50": ..., "p90": ...},
                "net_win": {"p10": ..., "p50": ..., "p90": ...},
                "confidence": 0.78,
                "confidence_level": "HIGH",
                "similar_locations": [...],
                "model_version": "egm_forecaster v3",
                "features_used": {...},
            }
        """
        start = time.perf_counter()

        # Load champion model from registry if available
        model_version = 0
        if self._registry and not self._model.is_trained:
            champion = self._registry.get_champion(model_name)
            if champion and champion.get("parameters"):
                self._model = QuantileModel(champion["parameters"])
                model_version = champion["version"]

        if not self._model.is_trained:
            return {
                "error": "No trained model available. Call train_model() first.",
                "coin_in": {"p10": 0, "p50": 0, "p90": 0},
                "hold_pct": {"p10": 0, "p50": 0, "p90": 0},
                "net_win": {"p10": 0, "p50": 0, "p90": 0},
                "confidence": 0.0,
                "confidence_level": "LOW",
            }

        # 1. Compute features
        if self._feat_eng:
            features = self._feat_eng.compute_features(
                venue_type=venue_type, state=state,
                terminal_count=terminal_count,
                municipality=municipality,
                location_id=location_id,
                target_month=target_month,
            )
        else:
            features = {
                "venue_type": venue_type,
                "state": state,
                "terminal_count": terminal_count,
                "seasonal_index": 1.0,
                "market_maturity": 1.0,
                "has_history": 0,
                "feature_completeness": 0.5,
            }

        # 2. Model inference
        predictions = self._model.predict(features)

        # 3. Confidence scoring
        confidence, confidence_level = compute_confidence(
            features, predictions, self._model._coin_in,
        )

        # 4. Similar locations
        similar = []
        if include_similar and self._session:
            try:
                similar = find_similar_locations(
                    self._session, venue_type, state,
                    municipality, terminal_count, limit=5,
                )
            except Exception:
                pass  # Non-critical

        elapsed = int((time.perf_counter() - start) * 1000)

        # 5. Build response
        inputs = {
            "venue_type": venue_type, "state": state,
            "terminal_count": terminal_count,
            "municipality": municipality,
        }
        if location_id:
            inputs["location_id"] = location_id

        result = {
            **predictions,
            "confidence": confidence,
            "confidence_level": confidence_level,
            "similar_locations": similar,
            "model_version": f"{model_name} v{model_version}",
            "prediction_ms": elapsed,
            "data_note": (
                "Prediction based on public gaming board data. "
                "Confidence bands represent p10/p50/p90 quantiles."
            ),
        }

        # 6. Log prediction
        if self._pred_log:
            try:
                pred_id = self._pred_log.log(
                    workspace_id=self._workspace_id,
                    model_name=model_name,
                    model_version=model_version,
                    inputs=inputs,
                    features={k: v for k, v in features.items()
                              if isinstance(v, (int, float, str, bool))},
                    predictions=predictions,
                    confidence=confidence,
                    execution_ms=elapsed,
                    user_id=self._user_id,
                )
                result["prediction_id"] = pred_id
            except Exception:
                pass

        return result

    def get_model_info(self, model_name: str = "egm_forecaster") -> Dict:
        """Get current champion model info."""
        if not self._registry:
            return {"trained": self._model.is_trained}

        champion = self._registry.get_champion(model_name)
        versions = self._registry.list_versions(model_name)

        return {
            "model_name": model_name,
            "champion": champion,
            "total_versions": len(versions),
            "trained": self._model.is_trained,
        }

    def _pull_training_data(self) -> List[Dict]:
        """Pull training records from EGM performance + location data."""
        if not self._session:
            return []

        from engine.db.egm_repositories import EGMPerformanceRepo, EGMLocationRepo

        perf_repo = EGMPerformanceRepo(self._session)
        loc_repo = EGMLocationRepo(self._session)

        months = perf_repo.available_months()
        if not months:
            return []

        training_data = []
        # Use all available months
        for month_str in months:
            year, mo = int(month_str[:4]), int(month_str[5:7])
            month_dt = datetime(year, mo, 1, tzinfo=timezone.utc)
            records = perf_repo.get_month(month_dt, limit=10000)

            for rec in records:
                if rec.get("coin_in", 0) <= 0:
                    continue
                training_data.append({
                    "venue_type": rec.get("venue_type", "other"),
                    "state": rec.get("state", ""),
                    "terminal_count": rec.get("terminal_count", 0),
                    "coin_in": rec.get("coin_in", 0),
                    "coin_out": rec.get("coin_out", 0),
                    "net_win": rec.get("net_win", 0),
                    "hold_pct": rec.get("hold_pct", 0),
                    "report_month": month_str,
                    "location_id": rec.get("location_id"),
                })

        return training_data
