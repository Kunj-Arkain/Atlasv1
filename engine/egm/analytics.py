"""
engine.egm.analytics — Data Health & Market Analytics
========================================================
Phase 3E: Completeness checks, anomaly detection, and
market-level aggregations from EGM performance data.

All computations are pure SQL aggregations via the repos —
no in-memory data accumulation. Scales to 1M+ rows.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.db.egm_repositories import (
    DataSourceRepo, EGMLocationRepo, EGMPerformanceRepo,
    IngestRunRepo,
)


class EGMAnalytics:
    """Health checks and market analytics for EGM data.

    Usage:
        analytics = EGMAnalytics(session)
        health = analytics.data_health_summary()
        anomalies = analytics.detect_anomalies(location_id=42)
        trends = analytics.market_trends(state="IL", months=12)
    """

    def __init__(self, session):
        self._session = session
        self._ds_repo = DataSourceRepo(session)
        self._loc_repo = EGMLocationRepo(session)
        self._perf_repo = EGMPerformanceRepo(session)
        self._run_repo = IngestRunRepo(session)

    # ═══════════════════════════════════════════════════════════
    # DATA HEALTH
    # ═══════════════════════════════════════════════════════════

    def data_health_summary(self) -> Dict[str, Any]:
        """Overall data quality summary across all sources."""
        sources = self._ds_repo.list_all()
        source_health = []

        for src in sources:
            runs = self._run_repo.list_runs(src["id"], limit=5)
            last_run = runs[0] if runs else None
            months = self._perf_repo.available_months(src["id"])

            source_health.append({
                "source": src["name"],
                "enabled": src["enabled"],
                "last_synced": src["last_synced_at"],
                "last_run_status": last_run["status"] if last_run else "never",
                "months_available": len(months),
                "latest_month": months[0] if months else None,
            })

        total_locations = self._loc_repo.count()
        all_months = self._perf_repo.available_months()

        return {
            "total_sources": len(sources),
            "total_locations": total_locations,
            "total_months": len(all_months),
            "latest_month": all_months[0] if all_months else None,
            "earliest_month": all_months[-1] if all_months else None,
            "sources": source_health,
        }

    def source_health(self, source_id: int) -> Dict[str, Any]:
        """Detailed health for a specific data source."""
        source = self._ds_repo.get(source_id)
        if not source:
            return {"error": "Source not found"}

        months = self._perf_repo.available_months(source_id)
        runs = self._run_repo.list_runs(source_id, limit=10)

        # Check for gaps in monthly data
        gaps = self._find_month_gaps(months) if len(months) >= 2 else []

        return {
            "source": source,
            "months_available": len(months),
            "month_range": {
                "earliest": months[-1] if months else None,
                "latest": months[0] if months else None,
            },
            "gaps": gaps,
            "recent_runs": runs[:5],
        }

    # ═══════════════════════════════════════════════════════════
    # ANOMALY DETECTION
    # ═══════════════════════════════════════════════════════════

    def detect_anomalies(
        self, location_id: int, z_threshold: float = 3.0,
    ) -> List[Dict]:
        """Detect anomalies in a location's performance history.

        Checks:
          - Coin-in Z-score > threshold vs rolling 12-month average
          - Hold% deviation > 2σ from location average
          - Terminal count changes
          - Net win sign flip (profitable ↔ unprofitable)
        """
        history = self._perf_repo.get_history(location_id, limit=60)
        if len(history) < 3:
            return []

        anomalies = []

        # Compute rolling stats from history
        coin_ins = [h["coin_in"] for h in history if h["coin_in"] > 0]
        hold_pcts = [h["hold_pct"] for h in history if h["hold_pct"] > 0]
        net_wins = [h["net_win"] for h in history]

        if len(coin_ins) >= 6:
            ci_mean = sum(coin_ins) / len(coin_ins)
            ci_std = _std(coin_ins)

            if ci_std > 0:
                latest_ci = history[0]["coin_in"]
                z = abs(latest_ci - ci_mean) / ci_std
                if z > z_threshold:
                    anomalies.append({
                        "type": "coin_in_outlier",
                        "severity": "high" if z > 5 else "medium",
                        "month": history[0]["report_month"],
                        "value": latest_ci,
                        "mean": round(ci_mean, 2),
                        "z_score": round(z, 2),
                        "detail": f"Coin-in Z-score {z:.1f} exceeds threshold {z_threshold}",
                    })

        if len(hold_pcts) >= 6:
            hp_mean = sum(hold_pcts) / len(hold_pcts)
            hp_std = _std(hold_pcts)

            if hp_std > 0:
                latest_hp = history[0]["hold_pct"]
                z = abs(latest_hp - hp_mean) / hp_std
                if z > 2.0:
                    anomalies.append({
                        "type": "hold_pct_deviation",
                        "severity": "medium",
                        "month": history[0]["report_month"],
                        "value": latest_hp,
                        "mean": round(hp_mean, 6),
                        "z_score": round(z, 2),
                        "detail": f"Hold% {latest_hp:.4%} deviates {z:.1f}σ from average {hp_mean:.4%}",
                    })

        # Terminal count change
        if len(history) >= 2:
            curr_tc = history[0]["terminal_count"]
            prev_tc = history[1]["terminal_count"]
            if curr_tc != prev_tc and prev_tc > 0:
                anomalies.append({
                    "type": "terminal_count_change",
                    "severity": "low",
                    "month": history[0]["report_month"],
                    "value": curr_tc,
                    "previous": prev_tc,
                    "change": curr_tc - prev_tc,
                    "detail": f"Terminals changed from {prev_tc} to {curr_tc}",
                })

        # Net win sign flip
        if len(net_wins) >= 2:
            if net_wins[0] * net_wins[1] < 0:
                direction = "unprofitable" if net_wins[0] < 0 else "profitable"
                anomalies.append({
                    "type": "net_win_sign_flip",
                    "severity": "high",
                    "month": history[0]["report_month"],
                    "value": net_wins[0],
                    "previous": net_wins[1],
                    "detail": f"Location went {direction} (NTI: ${net_wins[1]:,.2f} → ${net_wins[0]:,.2f})",
                })

        return anomalies

    # ═══════════════════════════════════════════════════════════
    # MARKET AGGREGATIONS
    # ═══════════════════════════════════════════════════════════

    def performance_summary(
        self, report_month: datetime,
        state: Optional[str] = None,
        venue_type: Optional[str] = None,
    ) -> Dict:
        """Market performance summary for a specific month."""
        by_state = self._perf_repo.aggregate_by_state(report_month)
        by_venue = self._perf_repo.aggregate_by_venue_type(report_month, state)

        # Filter by state if requested
        if state:
            by_state = [s for s in by_state if s["state"] == state]

        totals = {
            "total_locations": sum(s["location_count"] for s in by_state),
            "total_terminals": sum(s["total_terminals"] for s in by_state),
            "total_coin_in": sum(s["total_coin_in"] for s in by_state),
            "total_net_win": sum(s["total_net_win"] for s in by_state),
            "total_tax": sum(s["total_tax"] for s in by_state),
        }

        if totals["total_coin_in"] > 0:
            totals["overall_hold_pct"] = round(
                totals["total_net_win"] / totals["total_coin_in"], 6
            )
        else:
            totals["overall_hold_pct"] = 0

        return {
            "report_month": report_month.strftime("%Y-%m"),
            "totals": totals,
            "by_state": by_state,
            "by_venue_type": by_venue,
        }

    def location_trends(
        self, location_id: int, months: int = 12,
    ) -> Dict:
        """Time series trends for a single location."""
        history = self._perf_repo.get_history(location_id, limit=months)
        location = self._loc_repo.get(location_id)

        if not history:
            return {"location": location, "history": [], "trends": {}}

        # Compute basic trends
        coin_ins = [h["coin_in"] for h in history]
        net_wins = [h["net_win"] for h in history]

        trends = {}
        if len(history) >= 2:
            # Month-over-month change
            trends["coin_in_mom"] = round(
                (coin_ins[0] - coin_ins[1]) / coin_ins[1] * 100, 2
            ) if coin_ins[1] != 0 else 0
            trends["net_win_mom"] = round(
                (net_wins[0] - net_wins[1]) / abs(net_wins[1]) * 100, 2
            ) if net_wins[1] != 0 else 0

        if len(history) >= 12:
            # Year-over-year change
            trends["coin_in_yoy"] = round(
                (coin_ins[0] - coin_ins[11]) / coin_ins[11] * 100, 2
            ) if coin_ins[11] != 0 else 0
            trends["net_win_yoy"] = round(
                (net_wins[0] - net_wins[11]) / abs(net_wins[11]) * 100, 2
            ) if net_wins[11] != 0 else 0

        # Averages
        if coin_ins:
            trends["avg_coin_in_12m"] = round(sum(coin_ins) / len(coin_ins), 2)
            trends["avg_net_win_12m"] = round(sum(net_wins) / len(net_wins), 2)

        return {
            "location": location,
            "history": list(reversed(history)),  # Oldest first for charting
            "trends": trends,
        }

    def top_performers(
        self, report_month: datetime,
        state: Optional[str] = None,
        metric: str = "net_win",
        limit: int = 20,
    ) -> List[Dict]:
        """Top N locations by a given metric for a month."""
        records = self._perf_repo.get_month(
            report_month, state=state, limit=500
        )

        # Sort by metric
        valid = [r for r in records if r.get(metric) is not None]
        sorted_records = sorted(valid, key=lambda r: r.get(metric, 0), reverse=True)

        return sorted_records[:limit]

    # ═══════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _find_month_gaps(months: List[str]) -> List[str]:
        """Find missing months in a sorted (newest-first) list."""
        if len(months) < 2:
            return []

        gaps = []
        for i in range(len(months) - 1):
            curr = months[i]
            nxt = months[i + 1]
            # Parse YYYY-MM
            cy, cm = int(curr[:4]), int(curr[5:7])
            ny, nm = int(nxt[:4]), int(nxt[5:7])

            # Expected previous month
            em = cm - 1 if cm > 1 else 12
            ey = cy if cm > 1 else cy - 1

            if (ey, em) != (ny, nm):
                # There's a gap — find all missing months
                y, m = ey, em
                while (y, m) != (ny, nm) and len(gaps) < 100:
                    gaps.append(f"{y:04d}-{m:02d}")
                    m -= 1
                    if m < 1:
                        m = 12
                        y -= 1

        return gaps


def _std(values: List[float]) -> float:
    """Standard deviation (population)."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)
