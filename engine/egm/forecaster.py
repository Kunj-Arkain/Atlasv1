"""
engine.egm.forecaster — EGM Quantile Forecaster
===================================================
Phase 4B: Predicts coin_in, hold_pct, and net_win with p10/p50/p90
confidence bands, trained on public EGM performance data.

Architecture:
  - QuantileModel: parametric quantile estimator (pure Python, JSON-serializable)
  - Trains from venue-type group statistics + adjustment factors
  - Produces calibrated prediction intervals
  - Can be replaced with XGBoost/LightGBM quantile regression in production

Design: zero external dependencies (no numpy/sklearn/xgboost).
All model parameters are JSON-serializable dicts (no pickle).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# SEASONAL INDICES — same as features.py but duplicated here to
# avoid importing DB-dependent modules for pure-compute usage
# ═══════════════════════════════════════════════════════════════

_SEASONAL_INDEX = {
    1: 0.92, 2: 0.88, 3: 0.96, 4: 0.98,
    5: 1.02, 6: 1.06, 7: 1.08, 8: 1.07,
    9: 1.01, 10: 1.03, 11: 0.98, 12: 1.01,
}


# ═══════════════════════════════════════════════════════════════
# QUANTILE MODEL — Pure Python parametric estimator
# ═══════════════════════════════════════════════════════════════

@dataclass
class QuantileParams:
    """Learned parameters for one target variable.

    Stores group-level quantile statistics:
      groups[venue_type] = {
          "p10": value, "p50": value, "p90": value,
          "mean": value, "std": value, "count": int,
          "per_terminal": { "p10": ..., "p50": ..., "p90": ... }
      }

    Adjustment coefficients:
      terminal_coeff: multiplier per additional terminal above baseline
      seasonal_adj: monthly adjustment factors
      maturity_adj: market maturation adjustment
    """
    target: str = ""
    groups: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    terminal_baseline: int = 5
    terminal_coeff: float = 0.18  # +18% per terminal above baseline
    seasonal_adj: Dict[int, float] = field(default_factory=dict)
    global_p10: float = 0.0
    global_p50: float = 0.0
    global_p90: float = 0.0
    global_mean: float = 0.0
    global_std: float = 0.0
    total_samples: int = 0


class QuantileModel:
    """Parametric quantile prediction model.

    Prediction formula:
      base = group[venue_type].per_terminal.pXX * terminal_count
      adjusted = base * seasonal_index * market_maturity
      with_history = blend(adjusted, trailing_avg) if has_history

    The model is fully defined by its JSON-serializable params dict.
    """

    def __init__(self, params: Optional[Dict] = None):
        if params:
            self._coin_in = _dict_to_params(params.get("coin_in", {}))
            self._hold_pct = _dict_to_params(params.get("hold_pct", {}))
        else:
            self._coin_in = QuantileParams(target="coin_in")
            self._hold_pct = QuantileParams(target="hold_pct")
        self._trained = bool(params and params.get("coin_in", {}).get("groups"))

    @property
    def is_trained(self) -> bool:
        return self._trained

    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Train from a list of performance records.

        Each record should have:
            venue_type, terminal_count, coin_in, hold_pct, net_win,
            and optionally: state, municipality, report_month

        Returns training metrics.
        """
        if not training_data:
            return {"error": "No training data"}

        start = time.perf_counter()

        # Group by venue_type
        groups: Dict[str, List[Dict]] = {}
        for rec in training_data:
            vt = rec.get("venue_type", "other")
            groups.setdefault(vt, []).append(rec)

        # Train coin_in model
        self._coin_in = self._train_target(groups, "coin_in")
        self._coin_in.target = "coin_in"

        # Train hold_pct model
        self._hold_pct = self._train_target(groups, "hold_pct")
        self._hold_pct.target = "hold_pct"

        # Compute seasonal adjustments from data
        self._coin_in.seasonal_adj = self._compute_seasonal(training_data, "coin_in")
        self._hold_pct.seasonal_adj = {}  # Hold% is not very seasonal

        self._trained = True
        elapsed = int((time.perf_counter() - start) * 1000)

        metrics = {
            "training_samples": len(training_data),
            "venue_types": len(groups),
            "venue_type_counts": {vt: len(recs) for vt, recs in groups.items()},
            "coin_in_global_p50": self._coin_in.global_p50,
            "hold_pct_global_p50": self._hold_pct.global_p50,
            "training_ms": elapsed,
        }
        return metrics

    def predict(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """Generate quantile predictions from a feature vector.

        Returns:
            {
                "coin_in": {"p10": ..., "p50": ..., "p90": ...},
                "hold_pct": {"p10": ..., "p50": ..., "p90": ...},
                "net_win": {"p10": ..., "p50": ..., "p90": ...},
            }
        """
        if not self._trained:
            return self._default_prediction()

        venue_type = features.get("venue_type", "other")
        terminal_count = features.get("terminal_count", 5)
        seasonal = features.get("seasonal_index", 1.0)
        maturity = features.get("market_maturity", 1.0)
        has_history = features.get("has_history", 0)
        trailing_ci = features.get("trailing_avg_coin_in", 0.0)
        trailing_hp = features.get("trailing_avg_hold_pct", 0.0)

        # Predict coin_in
        ci = self._predict_target(
            self._coin_in, venue_type, terminal_count,
            seasonal, maturity, has_history, trailing_ci,
        )

        # Predict hold_pct
        hp = self._predict_target(
            self._hold_pct, venue_type, terminal_count,
            1.0, 1.0,  # No seasonal/maturity for hold%
            has_history, trailing_hp,
        )

        # Derive net_win = coin_in × hold_pct
        nw = {
            "p10": round(ci["p10"] * hp["p10"], 2),
            "p50": round(ci["p50"] * hp["p50"], 2),
            "p90": round(ci["p90"] * hp["p90"], 2),
        }

        return {"coin_in": ci, "hold_pct": hp, "net_win": nw}

    def to_params(self) -> Dict:
        """Serialize model to JSON-safe dict."""
        return {
            "coin_in": _params_to_dict(self._coin_in),
            "hold_pct": _params_to_dict(self._hold_pct),
        }

    # ── Training internals ────────────────────────────────

    def _train_target(
        self, groups: Dict[str, List[Dict]], target: str,
    ) -> QuantileParams:
        params = QuantileParams(target=target)
        all_values = []

        for vt, records in groups.items():
            values = [r[target] for r in records
                      if r.get(target) is not None and r[target] > 0]
            if not values:
                continue
            all_values.extend(values)

            values_sorted = sorted(values)
            n = len(values_sorted)

            # Per-terminal values
            per_terminal = []
            for r in records:
                tc = r.get("terminal_count", 5)
                v = r.get(target, 0)
                if tc > 0 and v > 0:
                    per_terminal.append(v / tc)
            per_terminal_sorted = sorted(per_terminal) if per_terminal else [0]
            pt_n = len(per_terminal_sorted)

            params.groups[vt] = {
                "p10": _quantile(values_sorted, 0.10),
                "p50": _quantile(values_sorted, 0.50),
                "p90": _quantile(values_sorted, 0.90),
                "mean": sum(values) / n,
                "std": _std(values),
                "count": n,
                "per_terminal": {
                    "p10": _quantile(per_terminal_sorted, 0.10),
                    "p50": _quantile(per_terminal_sorted, 0.50),
                    "p90": _quantile(per_terminal_sorted, 0.90),
                },
            }

        if all_values:
            all_sorted = sorted(all_values)
            params.global_p10 = _quantile(all_sorted, 0.10)
            params.global_p50 = _quantile(all_sorted, 0.50)
            params.global_p90 = _quantile(all_sorted, 0.90)
            params.global_mean = sum(all_values) / len(all_values)
            params.global_std = _std(all_values)
            params.total_samples = len(all_values)

        return params

    def _compute_seasonal(
        self, data: List[Dict], target: str,
    ) -> Dict[int, float]:
        """Compute month-of-year seasonal indices from data."""
        monthly_totals: Dict[int, List[float]] = {m: [] for m in range(1, 13)}

        for rec in data:
            rm = rec.get("report_month")
            val = rec.get(target, 0)
            if rm and val > 0:
                if isinstance(rm, str):
                    month = int(rm[5:7]) if len(rm) >= 7 else 1
                elif isinstance(rm, datetime):
                    month = rm.month
                else:
                    continue
                monthly_totals[month].append(val)

        # Compute index = month_avg / global_avg
        all_vals = [v for vs in monthly_totals.values() for v in vs]
        if not all_vals:
            return dict(_SEASONAL_INDEX)

        global_avg = sum(all_vals) / len(all_vals)
        if global_avg <= 0:
            return dict(_SEASONAL_INDEX)

        indices = {}
        for month, vals in monthly_totals.items():
            if vals:
                month_avg = sum(vals) / len(vals)
                indices[month] = round(month_avg / global_avg, 4)
            else:
                indices[month] = _SEASONAL_INDEX.get(month, 1.0)

        return indices

    # ── Prediction internals ──────────────────────────────

    def _predict_target(
        self, params: QuantileParams, venue_type: str,
        terminal_count: int, seasonal: float, maturity: float,
        has_history: int, trailing_avg: float,
    ) -> Dict[str, float]:
        """Predict p10/p50/p90 for a single target."""
        group = params.groups.get(venue_type)
        if not group:
            group = params.groups.get("other")
        if not group:
            # Fall back to global
            return {
                "p10": round(params.global_p10 * seasonal * maturity, 4),
                "p50": round(params.global_p50 * seasonal * maturity, 4),
                "p90": round(params.global_p90 * seasonal * maturity, 4),
            }

        pt = group["per_terminal"]

        # Base prediction from per-terminal quantiles
        base = {
            "p10": pt["p10"] * terminal_count,
            "p50": pt["p50"] * terminal_count,
            "p90": pt["p90"] * terminal_count,
        }

        # Apply seasonal and maturity adjustments
        for q in ("p10", "p50", "p90"):
            base[q] *= seasonal * maturity

        # Blend with historical if available (70% model / 30% history)
        if has_history and trailing_avg > 0:
            for q in ("p10", "p50", "p90"):
                base[q] = base[q] * 0.7 + trailing_avg * 0.3

        # Round appropriately
        if params.target == "hold_pct":
            for q in base:
                base[q] = round(base[q], 6)
        else:
            for q in base:
                base[q] = round(base[q], 2)

        return base

    def _default_prediction(self) -> Dict:
        return {
            "coin_in": {"p10": 0, "p50": 0, "p90": 0},
            "hold_pct": {"p10": 0, "p50": 0, "p90": 0},
            "net_win": {"p10": 0, "p50": 0, "p90": 0},
        }


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE SCORER
# ═══════════════════════════════════════════════════════════════

def compute_confidence(
    features: Dict[str, Any],
    predictions: Dict[str, Any],
    model_params: QuantileParams,
) -> Tuple[float, str]:
    """Compute prediction confidence score (0.0–1.0).

    Factors:
      - Feature completeness (25%)
      - Similar location count in training data (25%)
      - Prediction interval width relative to median (25%)
      - Data recency / has history (25%)

    Returns: (score, level) where level is LOW/MEDIUM/HIGH
    """
    scores = []

    # 1. Feature completeness
    completeness = features.get("feature_completeness", 0.5)
    scores.append(completeness)

    # 2. Similar location count
    venue_type = features.get("venue_type", "other")
    group = model_params.groups.get(venue_type, {})
    sample_count = group.get("count", 0)
    # 100+ samples → 1.0, 10 samples → 0.5, 0 → 0.1
    count_score = min(1.0, max(0.1, math.log10(max(sample_count, 1)) / 2))
    scores.append(count_score)

    # 3. Prediction interval width (narrow = confident)
    ci_pred = predictions.get("coin_in", {})
    p50 = ci_pred.get("p50", 0)
    p10 = ci_pred.get("p10", 0)
    p90 = ci_pred.get("p90", 0)
    if p50 > 0:
        width_ratio = (p90 - p10) / p50
        # Narrow band (< 0.5x median) → 1.0, wide (> 2x) → 0.2
        width_score = max(0.2, 1.0 - width_ratio / 3.0)
    else:
        width_score = 0.2
    scores.append(width_score)

    # 4. Has history
    has_history = features.get("has_history", 0)
    months = features.get("months_of_data", 0)
    if has_history and months >= 12:
        history_score = 1.0
    elif has_history and months >= 6:
        history_score = 0.7
    elif has_history:
        history_score = 0.5
    else:
        history_score = 0.3
    scores.append(history_score)

    # Weighted average
    confidence = round(sum(scores) / len(scores), 4)
    confidence = max(0.0, min(1.0, confidence))

    if confidence >= 0.7:
        level = "HIGH"
    elif confidence >= 0.4:
        level = "MEDIUM"
    else:
        level = "LOW"

    return confidence, level


# ═══════════════════════════════════════════════════════════════
# SIMILAR LOCATIONS FINDER
# ═══════════════════════════════════════════════════════════════

def find_similar_locations(
    session,
    venue_type: str,
    state: str,
    municipality: str = "",
    terminal_count: int = 5,
    limit: int = 5,
) -> List[Dict]:
    """Find similar locations for sanity-check display.

    Prioritizes: same venue_type + same municipality > same state.
    Returns locations with their average net_win.
    """
    from engine.db.egm_repositories import EGMLocationRepo, EGMPerformanceRepo

    loc_repo = EGMLocationRepo(session)
    perf_repo = EGMPerformanceRepo(session)

    # Search same municipality first, then broaden
    candidates = []
    if municipality:
        candidates = loc_repo.search(
            state=state, venue_type=venue_type,
            municipality=municipality, limit=limit * 2,
        )
    if len(candidates) < limit:
        broader = loc_repo.search(
            state=state, venue_type=venue_type, limit=limit * 3,
        )
        seen = {c["id"] for c in candidates}
        for loc in broader:
            if loc["id"] not in seen:
                candidates.append(loc)

    # Compute avg performance for each candidate
    results = []
    for loc in candidates[:limit * 2]:
        history = perf_repo.get_history(loc["id"], limit=12)
        if not history:
            continue
        net_wins = [h["net_win"] for h in history if h["net_win"] != 0]
        coin_ins = [h["coin_in"] for h in history if h["coin_in"] > 0]
        if not net_wins:
            continue

        avg_nw = round(sum(net_wins) / len(net_wins), 2)
        avg_ci = round(sum(coin_ins) / len(coin_ins), 2) if coin_ins else 0

        results.append({
            "name": loc["name"],
            "municipality": loc["municipality"],
            "venue_type": loc["venue_type"],
            "terminal_count": history[0].get("terminal_count", 0),
            "monthly_net_win_avg": avg_nw,
            "monthly_coin_in_avg": avg_ci,
            "months_of_data": len(history),
        })

    # Sort by terminal_count proximity, then by net_win
    results.sort(key=lambda r: (
        abs(r["terminal_count"] - terminal_count),
        -r["monthly_net_win_avg"],
    ))

    return results[:limit]


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _quantile(sorted_values: List[float], q: float) -> float:
    """Compute quantile from a sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    idx = q * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return round(sorted_values[lo], 6)
    frac = idx - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac, 6)


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return round(math.sqrt(variance), 6)


def _params_to_dict(params: QuantileParams) -> Dict:
    return {
        "target": params.target,
        "groups": params.groups,
        "terminal_baseline": params.terminal_baseline,
        "terminal_coeff": params.terminal_coeff,
        "seasonal_adj": {str(k): v for k, v in params.seasonal_adj.items()},
        "global_p10": params.global_p10,
        "global_p50": params.global_p50,
        "global_p90": params.global_p90,
        "global_mean": params.global_mean,
        "global_std": params.global_std,
        "total_samples": params.total_samples,
    }


def _dict_to_params(d: Dict) -> QuantileParams:
    if not d:
        return QuantileParams()
    return QuantileParams(
        target=d.get("target", ""),
        groups=d.get("groups", {}),
        terminal_baseline=d.get("terminal_baseline", 5),
        terminal_coeff=d.get("terminal_coeff", 0.18),
        seasonal_adj={int(k): v for k, v in d.get("seasonal_adj", {}).items()},
        global_p10=d.get("global_p10", 0),
        global_p50=d.get("global_p50", 0),
        global_p90=d.get("global_p90", 0),
        global_mean=d.get("global_mean", 0),
        global_std=d.get("global_std", 0),
        total_samples=d.get("total_samples", 0),
    )
