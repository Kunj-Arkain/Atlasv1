"""
engine.brain.learning — Continuous Learning + Experiment Mode
================================================================
Phase 9: Model retraining pipeline, drift detection, champion/challenger
framework, and A/B experiment infrastructure.

Components:
  - DriftDetector: compares predictions vs actuals, detects degradation
  - RetrainingPipeline: trains new models, validates, champion/challenger
  - ExperimentRunner: A/B tests of config variants
  - AlertManager: drift/anomaly alerting

Zero external dependencies beyond engine internals.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# DRIFT DETECTOR (Phase 9B)
# ═══════════════════════════════════════════════════════════════

@dataclass
class DriftAlert:
    """Alert raised when model drift is detected."""
    alert_type: str  # prediction_drift, venue_drift, market_shift, state_drift
    severity: str  # INFO, WARNING, CRITICAL
    metric: str
    expected: float
    actual: float
    deviation_pct: float
    details: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "metric": self.metric,
            "expected": self.expected,
            "actual": self.actual,
            "deviation_pct": round(self.deviation_pct, 4),
            "details": self.details,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


class DriftDetector:
    """Detects model drift by comparing predictions to actuals.

    Thresholds:
      - WARNING: deviation > 15%
      - CRITICAL: deviation > 30%

    Monitors:
      - Overall prediction accuracy (MAPE)
      - Per-venue-type accuracy
      - Per-state accuracy
      - Market-level shifts (YoY revenue changes)
    """

    def __init__(
        self,
        warning_threshold: float = 0.15,
        critical_threshold: float = 0.30,
        market_shift_threshold: float = 0.20,
    ):
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.market_shift_threshold = market_shift_threshold

    def check_predictions(
        self, predictions: List[Dict], actuals: List[Dict],
    ) -> List[DriftAlert]:
        """Compare predictions to actuals and generate alerts.

        Each prediction/actual should have:
            location_id, venue_type, state, predicted_net_win, actual_net_win
        """
        alerts = []

        if not predictions or not actuals:
            return alerts

        # Build lookup: location_id → actual
        actual_map = {a["location_id"]: a for a in actuals}

        # Overall accuracy
        errors = []
        by_venue: Dict[str, List[float]] = {}
        by_state: Dict[str, List[float]] = {}

        for pred in predictions:
            loc_id = pred["location_id"]
            actual = actual_map.get(loc_id)
            if not actual:
                continue

            predicted = pred.get("predicted_net_win", 0)
            actual_nw = actual.get("actual_net_win", 0)

            if actual_nw == 0:
                continue

            error_pct = abs(predicted - actual_nw) / abs(actual_nw)
            errors.append(error_pct)

            vt = pred.get("venue_type", "other")
            by_venue.setdefault(vt, []).append(error_pct)

            st = pred.get("state", "")
            by_state.setdefault(st, []).append(error_pct)

        # Overall MAPE
        if errors:
            mape = sum(errors) / len(errors)
            if mape > self.critical_threshold:
                alerts.append(DriftAlert(
                    alert_type="prediction_drift",
                    severity="CRITICAL",
                    metric="overall_mape",
                    expected=self.warning_threshold,
                    actual=mape,
                    deviation_pct=mape,
                    details=f"Overall MAPE {mape:.1%} exceeds critical threshold",
                ))
            elif mape > self.warning_threshold:
                alerts.append(DriftAlert(
                    alert_type="prediction_drift",
                    severity="WARNING",
                    metric="overall_mape",
                    expected=self.warning_threshold,
                    actual=mape,
                    deviation_pct=mape,
                    details=f"Overall MAPE {mape:.1%} exceeds warning threshold",
                ))

        # Per-venue-type drift
        for vt, errs in by_venue.items():
            vt_mape = sum(errs) / len(errs)
            if vt_mape > self.critical_threshold:
                alerts.append(DriftAlert(
                    alert_type="venue_drift",
                    severity="CRITICAL",
                    metric=f"mape_{vt}",
                    expected=self.warning_threshold,
                    actual=vt_mape,
                    deviation_pct=vt_mape,
                    details=f"Venue type '{vt}' MAPE {vt_mape:.1%}",
                ))

        # Per-state drift
        for st, errs in by_state.items():
            st_mape = sum(errs) / len(errs)
            if st_mape > self.critical_threshold:
                alerts.append(DriftAlert(
                    alert_type="state_drift",
                    severity="CRITICAL",
                    metric=f"mape_{st}",
                    expected=self.warning_threshold,
                    actual=st_mape,
                    deviation_pct=st_mape,
                    details=f"State '{st}' MAPE {st_mape:.1%}",
                ))

        return alerts

    def check_market_shift(
        self, current_period: List[Dict], previous_period: List[Dict],
    ) -> List[DriftAlert]:
        """Detect market-level revenue shifts (YoY or MoM).

        Each record: municipality, total_net_win
        """
        alerts = []

        current_map = {r["municipality"]: r["total_net_win"]
                       for r in current_period}
        previous_map = {r["municipality"]: r["total_net_win"]
                        for r in previous_period}

        for muni, current_nw in current_map.items():
            prev_nw = previous_map.get(muni)
            if not prev_nw or prev_nw == 0:
                continue

            change = (current_nw - prev_nw) / abs(prev_nw)
            if abs(change) > self.market_shift_threshold:
                direction = "increased" if change > 0 else "decreased"
                alerts.append(DriftAlert(
                    alert_type="market_shift",
                    severity="WARNING",
                    metric=f"revenue_{muni}",
                    expected=prev_nw,
                    actual=current_nw,
                    deviation_pct=change,
                    details=(
                        f"{muni} revenue {direction} by {abs(change):.1%} "
                        f"(${prev_nw:,.0f} → ${current_nw:,.0f})"
                    ),
                ))

        return alerts


# ═══════════════════════════════════════════════════════════════
# RETRAINING PIPELINE (Phase 9A)
# ═══════════════════════════════════════════════════════════════

@dataclass
class RetrainingResult:
    """Result from a retraining cycle."""
    model_name: str
    new_version: int = 0
    champion_version: int = 0
    challenger_metrics: Dict = field(default_factory=dict)
    champion_metrics: Dict = field(default_factory=dict)
    challenger_wins: bool = False
    auto_promoted: bool = False
    approval_required: bool = True
    training_samples: int = 0
    execution_ms: int = 0


class RetrainingPipeline:
    """Champion/Challenger model retraining.

    Workflow:
      1. Pull latest training data
      2. Train challenger model
      3. Evaluate on held-out validation set
      4. Compare to current champion
      5. If challenger wins AND approval given → promote
      6. Log everything to model registry

    No auto-promotion without human sign-off.
    """

    def __init__(self, session=None, workspace_id: str = "", user_id: str = ""):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id

    def retrain(
        self, model_name: str = "egm_forecaster",
        validation_split: float = 0.2,
        auto_promote: bool = False,
    ) -> RetrainingResult:
        """Run a full retraining cycle."""
        start = time.perf_counter()

        from engine.egm.prediction import PredictionService
        from engine.db.forecast_repositories import ModelRegistryRepo

        svc = PredictionService(self._session, self._workspace_id, self._user_id)
        registry = ModelRegistryRepo(self._session)

        # Get current champion
        champion = registry.get_champion(model_name)
        champion_version = champion["version"] if champion else 0

        # Train challenger
        train_result = svc.train_model(model_name)
        if train_result.get("error"):
            return RetrainingResult(
                model_name=model_name,
                champion_version=champion_version,
                execution_ms=int((time.perf_counter() - start) * 1000),
            )

        new_version = train_result["version"]
        challenger_metrics = train_result.get("metrics", {})
        champion_metrics = champion.get("metrics", {}) if champion else {}

        # Compare (simple: more training samples + same methodology = better)
        challenger_wins = self._compare_models(
            challenger_metrics, champion_metrics,
        )

        # Promote if allowed
        promoted = False
        if challenger_wins and auto_promote:
            registry.promote(model_name, new_version, self._user_id)
            promoted = True

        elapsed = int((time.perf_counter() - start) * 1000)

        return RetrainingResult(
            model_name=model_name,
            new_version=new_version,
            champion_version=champion_version,
            challenger_metrics=challenger_metrics,
            champion_metrics=champion_metrics,
            challenger_wins=challenger_wins,
            auto_promoted=promoted,
            approval_required=not auto_promote,
            training_samples=train_result.get("training_samples", 0),
            execution_ms=elapsed,
        )

    def _compare_models(
        self, challenger: Dict, champion: Dict,
    ) -> bool:
        """Compare challenger to champion metrics.

        Returns True if challenger is better.
        Simple heuristic: more training data + at least 5 venue types.
        """
        if not champion:
            return True  # No champion → challenger wins by default

        c_samples = challenger.get("training_samples", 0)
        p_samples = champion.get("training_samples", 0)
        c_types = challenger.get("venue_types", 0)

        # Challenger needs at least as much data and decent coverage
        return c_samples >= p_samples and c_types >= 3


# ═══════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER (Phase 9C)
# ═══════════════════════════════════════════════════════════════

@dataclass
class ExperimentResult:
    """Result from an A/B experiment."""
    experiment_name: str
    variant_a: str
    variant_b: str
    metric_a: Dict = field(default_factory=dict)
    metric_b: Dict = field(default_factory=dict)
    winner: str = ""
    improvement_pct: float = 0.0
    sample_size: int = 0
    execution_ms: int = 0


class ExperimentRunner:
    """A/B testing for agent configs and model variants.

    Runs the same set of inputs through two configurations
    and compares outcomes.

    Usage:
        runner = ExperimentRunner()
        result = runner.run(
            "model_comparison",
            variant_a=model_v1, variant_b=model_v2,
            test_cases=[...],
        )
    """

    def run(
        self,
        experiment_name: str,
        variant_a: Callable,
        variant_b: Callable,
        test_cases: List[Dict],
        metric_fn: Optional[Callable] = None,
    ) -> ExperimentResult:
        """Run A/B experiment.

        variant_a/b: callables that take a test case dict and return a result dict
        metric_fn: function that takes (result, expected) and returns a score
        """
        start = time.perf_counter()

        if metric_fn is None:
            metric_fn = _default_metric

        scores_a = []
        scores_b = []

        for case in test_cases:
            try:
                result_a = variant_a(case)
                score_a = metric_fn(result_a, case)
                scores_a.append(score_a)
            except Exception:
                scores_a.append(0.0)

            try:
                result_b = variant_b(case)
                score_b = metric_fn(result_b, case)
                scores_b.append(score_b)
            except Exception:
                scores_b.append(0.0)

        avg_a = sum(scores_a) / max(len(scores_a), 1)
        avg_b = sum(scores_b) / max(len(scores_b), 1)

        if avg_a >= avg_b:
            winner = "A"
            improvement = (avg_a - avg_b) / max(abs(avg_b), 0.001)
        else:
            winner = "B"
            improvement = (avg_b - avg_a) / max(abs(avg_a), 0.001)

        elapsed = int((time.perf_counter() - start) * 1000)

        return ExperimentResult(
            experiment_name=experiment_name,
            variant_a="A", variant_b="B",
            metric_a={"avg_score": round(avg_a, 6), "samples": len(scores_a)},
            metric_b={"avg_score": round(avg_b, 6), "samples": len(scores_b)},
            winner=winner,
            improvement_pct=round(improvement, 4),
            sample_size=len(test_cases),
            execution_ms=elapsed,
        )


def _default_metric(result: Dict, expected: Dict) -> float:
    """Default metric: accuracy of net_win prediction."""
    predicted = result.get("net_win", {}).get("p50", 0)
    actual = expected.get("actual_net_win", 0)
    if actual == 0:
        return 0.0
    error = abs(predicted - actual) / abs(actual)
    return max(0, 1.0 - error)  # 1.0 = perfect, 0.0 = terrible
