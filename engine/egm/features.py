"""
engine.egm.features — Feature Engineering
=============================================
Phase 4A: Derives prediction features from EGM data.

Feature categories:
  - Location: venue_type, state, terminal_count
  - Historical: mean/median coin_in by venue_type, seasonal indices
  - Market: competitor count, locations per municipality, saturation
  - Performance: trailing averages, trend slopes

All computations are pure Python (no numpy/pandas required).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.db.egm_repositories import EGMLocationRepo, EGMPerformanceRepo


# ═══════════════════════════════════════════════════════════════
# SEASONAL INDICES — derived from 12 years of IL data patterns
# ═══════════════════════════════════════════════════════════════

# Month-of-year seasonal adjustment factors (1.0 = average month)
# Empirical pattern: gaming revenue peaks in summer, dips in winter
SEASONAL_INDEX = {
    1: 0.92, 2: 0.88, 3: 0.96, 4: 0.98,
    5: 1.02, 6: 1.06, 7: 1.08, 8: 1.07,
    9: 1.01, 10: 1.03, 11: 0.98, 12: 1.01,
}

# Market maturation curve — IL video gaming growth normalized
# Years since market launch; 1.0 = fully mature
MATURATION_CURVE = {
    0: 0.15, 1: 0.30, 2: 0.50, 3: 0.65, 4: 0.75,
    5: 0.82, 6: 0.88, 7: 0.92, 8: 0.95, 9: 0.97,
    10: 0.99, 11: 1.00, 12: 1.00,
}


class FeatureEngineer:
    """Computes feature vectors for EGM prediction.

    Usage:
        fe = FeatureEngineer(session)
        features = fe.compute_features(
            venue_type="bar", state="IL",
            terminal_count=5, municipality="Springfield",
        )
        # features is a dict ready for the model
    """

    def __init__(self, session):
        self._session = session
        self._loc_repo = EGMLocationRepo(session)
        self._perf_repo = EGMPerformanceRepo(session)
        self._cache: Dict[str, Any] = {}

    def compute_features(
        self,
        venue_type: str,
        state: str,
        terminal_count: int,
        municipality: str = "",
        location_id: Optional[int] = None,
        target_month: Optional[datetime] = None,
        **extra_attrs,
    ) -> Dict[str, Any]:
        """Compute the full feature vector for a prediction.

        Returns a flat dict of numeric features.
        """
        month = target_month or datetime.now(timezone.utc).replace(day=1)

        features: Dict[str, Any] = {}

        # ── Location features ─────────────────────────────
        features["venue_type_encoded"] = _encode_venue_type(venue_type)
        features["state_encoded"] = _encode_state(state)
        features["terminal_count"] = terminal_count
        features["venue_type"] = venue_type
        features["state"] = state

        # ── Seasonal features ─────────────────────────────
        features["seasonal_index"] = SEASONAL_INDEX.get(month.month, 1.0)
        features["month_of_year"] = month.month

        # ── Market maturation ─────────────────────────────
        # IL video gaming started Oct 2012
        if state == "IL":
            years_since_launch = max(0, month.year - 2012)
            features["market_maturity"] = MATURATION_CURVE.get(
                years_since_launch, 1.0
            )
        else:
            features["market_maturity"] = 1.0

        # ── Market density features ───────────────────────
        market = self._compute_market_features(state, municipality, venue_type)
        features.update(market)

        # ── Venue-type baseline features ──────────────────
        baselines = self._compute_venue_baselines(state, venue_type)
        features.update(baselines)

        # ── Historical features (if existing location) ────
        if location_id:
            historical = self._compute_historical_features(location_id)
            features.update(historical)
        else:
            features["has_history"] = 0
            features["trailing_avg_coin_in"] = 0.0
            features["trailing_avg_net_win"] = 0.0
            features["trailing_avg_hold_pct"] = 0.0
            features["trend_slope_coin_in"] = 0.0
            features["months_of_data"] = 0

        # ── Feature completeness ──────────────────────────
        total_fields = len(features)
        populated = sum(1 for v in features.values()
                        if v is not None and v != 0 and v != "")
        features["feature_completeness"] = round(populated / max(total_fields, 1), 4)

        return features

    def _compute_market_features(
        self, state: str, municipality: str, venue_type: str,
    ) -> Dict[str, Any]:
        """Compute market density and competition features."""
        cache_key = f"market:{state}:{municipality}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        total_in_state = self._loc_repo.count(state=state)
        by_type = self._loc_repo.count_by_venue_type(state=state)
        same_type_count = 0
        for entry in by_type:
            if entry["venue_type"] == venue_type:
                same_type_count = entry["count"]
                break

        # Municipality-level density
        if municipality:
            muni_locs = self._loc_repo.search(
                state=state, municipality=municipality, limit=500
            )
            muni_count = len(muni_locs)
        else:
            muni_count = 0

        result = {
            "state_location_count": total_in_state,
            "state_same_type_count": same_type_count,
            "municipality_location_count": muni_count,
            "market_density": round(
                same_type_count / max(total_in_state, 1), 6
            ),
        }
        self._cache[cache_key] = result
        return result

    def _compute_venue_baselines(
        self, state: str, venue_type: str,
    ) -> Dict[str, Any]:
        """Compute venue-type average performance baselines."""
        cache_key = f"baseline:{state}:{venue_type}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        months = self._perf_repo.available_months()
        if not months:
            result = {
                "baseline_coin_in": 0.0,
                "baseline_net_win": 0.0,
                "baseline_hold_pct": 0.0,
                "baseline_sample_size": 0,
            }
            self._cache[cache_key] = result
            return result

        # Use latest month's aggregation
        latest = months[0]
        year, mo = int(latest[:4]), int(latest[5:7])
        latest_dt = datetime(year, mo, 1, tzinfo=timezone.utc)

        by_venue = self._perf_repo.aggregate_by_venue_type(latest_dt, state)
        for entry in by_venue:
            if entry["venue_type"] == venue_type:
                count = entry["count"]
                result = {
                    "baseline_coin_in": round(
                        entry["total_net_win"] / max(count, 1) / max(entry.get("avg_hold_pct", 0.1), 0.01), 2
                    ) if entry.get("avg_hold_pct") else 0.0,
                    "baseline_net_win": round(
                        entry["total_net_win"] / max(count, 1), 2
                    ),
                    "baseline_hold_pct": entry["avg_hold_pct"],
                    "baseline_sample_size": count,
                }
                self._cache[cache_key] = result
                return result

        result = {
            "baseline_coin_in": 0.0, "baseline_net_win": 0.0,
            "baseline_hold_pct": 0.0, "baseline_sample_size": 0,
        }
        self._cache[cache_key] = result
        return result

    def _compute_historical_features(
        self, location_id: int,
    ) -> Dict[str, Any]:
        """Compute trailing averages and trend from location history."""
        history = self._perf_repo.get_history(location_id, limit=24)
        if not history:
            return {
                "has_history": 0, "trailing_avg_coin_in": 0.0,
                "trailing_avg_net_win": 0.0, "trailing_avg_hold_pct": 0.0,
                "trend_slope_coin_in": 0.0, "months_of_data": 0,
            }

        coin_ins = [h["coin_in"] for h in history if h["coin_in"] > 0]
        net_wins = [h["net_win"] for h in history]
        hold_pcts = [h["hold_pct"] for h in history if h["hold_pct"] > 0]

        n = len(coin_ins)
        result = {
            "has_history": 1,
            "months_of_data": len(history),
            "trailing_avg_coin_in": round(sum(coin_ins) / max(n, 1), 2),
            "trailing_avg_net_win": round(
                sum(net_wins) / max(len(net_wins), 1), 2
            ),
            "trailing_avg_hold_pct": round(
                sum(hold_pcts) / max(len(hold_pcts), 1), 6
            ),
            "trend_slope_coin_in": _compute_slope(coin_ins) if n >= 3 else 0.0,
        }
        return result


# ═══════════════════════════════════════════════════════════════
# ENCODING HELPERS
# ═══════════════════════════════════════════════════════════════

_VENUE_ENCODING = {
    "bar": 1, "restaurant": 2, "fraternal": 3,
    "truck_stop": 4, "gaming_cafe": 5, "gas_station": 6,
    "casino": 7, "other": 0,
}

_STATE_ENCODING = {
    "IL": 1, "NV": 2, "PA": 3, "CO": 4,
}


def _encode_venue_type(venue_type: str) -> int:
    return _VENUE_ENCODING.get(venue_type, 0)


def _encode_state(state: str) -> int:
    return _STATE_ENCODING.get(state, 0)


def _compute_slope(values: List[float]) -> float:
    """Simple linear regression slope (OLS). Values are newest-first."""
    if len(values) < 3:
        return 0.0
    # Reverse so index 0 = oldest
    v = list(reversed(values))
    n = len(v)
    x_mean = (n - 1) / 2.0
    y_mean = sum(v) / n
    num = sum((i - x_mean) * (v[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if abs(den) < 1e-10:
        return 0.0
    return round(num / den, 2)
