"""
engine.portfolio.analytics — Portfolio Analytics & Dashboard
================================================================
Phase 7B/C: Portfolio-level aggregations, concentration tracking,
Herfindahl index, debt maturity ladder, and new-deal impact analysis.

Usage:
    analytics = PortfolioAnalytics(session, workspace_id="ws1")
    dashboard = analytics.dashboard()
    impact = analytics.new_deal_impact({
        "name": "New Gas Station", "state": "IL",
        "current_value": 800000, "has_gaming": True,
    })
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from engine.db.portfolio_repositories import (
    PortfolioAssetRepo, PortfolioDebtRepo,
    PortfolioNOIRepo, PortfolioEGMExposureRepo,
)


class PortfolioAnalytics:
    """Portfolio-level analytics and dashboard computations."""

    def __init__(self, session, workspace_id: str = ""):
        self._session = session
        self._ws = workspace_id
        self._assets = PortfolioAssetRepo(session)
        self._debt = PortfolioDebtRepo(session)
        self._noi = PortfolioNOIRepo(session)
        self._egm = PortfolioEGMExposureRepo(session)

    def dashboard(self) -> Dict[str, Any]:
        """Full portfolio dashboard snapshot."""
        assets = self._assets.list_assets(self._ws)

        total_value = sum(a["current_value"] for a in assets)
        total_cost = sum(a["acquisition_cost"] for a in assets)
        total_debt = self._debt.total_debt(self._ws)
        total_equity = total_value - total_debt

        by_state = self._assets.count_by_state(self._ws)
        by_type = self._assets.count_by_property_type(self._ws)
        maturity = self._debt.maturity_ladder(self._ws)
        gaming = self._egm.total_gaming_exposure(self._ws)

        # Ownership split
        owned = sum(1 for a in assets if a["ownership_type"] == "owned")
        financed = sum(1 for a in assets if a["ownership_type"] == "financed")

        # Contract type split
        contract_types: Dict[str, int] = {}
        for a in assets:
            ct = a["contract_type"] or "none"
            contract_types[ct] = contract_types.get(ct, 0) + 1

        # Concentration indices
        state_hhi = _herfindahl(
            [s["total_value"] for s in by_state], total_value
        )
        type_hhi = _herfindahl(
            [t["total_value"] for t in by_type], total_value
        )

        return {
            "summary": {
                "total_assets": len(assets),
                "total_value": round(total_value, 2),
                "total_acquisition_cost": round(total_cost, 2),
                "total_debt": round(total_debt, 2),
                "total_equity": round(total_equity, 2),
                "leverage_ratio": round(total_debt / max(total_value, 1), 4),
                "debt_to_equity": round(
                    total_debt / max(total_equity, 1), 4
                ) if total_equity > 0 else 0.0,
            },
            "by_state": by_state,
            "by_property_type": by_type,
            "ownership_split": {
                "owned": owned, "financed": financed,
                "total": len(assets),
            },
            "contract_types": contract_types,
            "debt_maturity_ladder": maturity,
            "gaming_exposure": gaming,
            "concentration": {
                "state_hhi": state_hhi,
                "property_type_hhi": type_hhi,
                "state_concentrated": state_hhi > 0.25,
                "type_concentrated": type_hhi > 0.25,
            },
        }

    def new_deal_impact(self, deal: Dict[str, Any]) -> Dict[str, Any]:
        """Compute portfolio impact of adding a new deal.

        Args:
            deal: Dict with name, state, current_value, property_type,
                  has_gaming, ownership_type, etc.

        Returns:
            Impact analysis: concentration delta, leverage shift, warnings.
        """
        assets = self._assets.list_assets(self._ws)
        total_value = sum(a["current_value"] for a in assets)
        total_debt = self._debt.total_debt(self._ws)

        deal_value = deal.get("current_value", 0)
        deal_state = deal.get("state", "")
        deal_type = deal.get("property_type", "")
        deal_debt = deal.get("debt_amount", 0)

        new_total = total_value + deal_value
        new_debt = total_debt + deal_debt
        new_equity = new_total - new_debt

        # State concentration before/after
        state_values: Dict[str, float] = {}
        for a in assets:
            s = a["state"] or "unknown"
            state_values[s] = state_values.get(s, 0) + a["current_value"]
        state_values_before = dict(state_values)
        state_values[deal_state] = state_values.get(deal_state, 0) + deal_value

        hhi_before = _herfindahl(
            list(state_values_before.values()), total_value
        )
        hhi_after = _herfindahl(list(state_values.values()), new_total)

        # State exposure percentage
        state_exposure_after = round(
            state_values.get(deal_state, 0) / max(new_total, 1), 4
        )

        # Leverage shift
        leverage_before = round(total_debt / max(total_value, 1), 4)
        leverage_after = round(new_debt / max(new_total, 1), 4)

        # Warnings
        warnings = []
        if state_exposure_after > 0.50:
            warnings.append(
                f"High state concentration: {deal_state} would be "
                f"{state_exposure_after:.0%} of portfolio"
            )
        if hhi_after > 0.25:
            warnings.append(
                f"Portfolio HHI would increase to {hhi_after:.4f} "
                f"(concentrated)"
            )
        if leverage_after > 0.70:
            warnings.append(
                f"Leverage ratio would reach {leverage_after:.0%}"
            )
        if deal_value > total_value * 0.30 and total_value > 0:
            warnings.append(
                f"Single-deal concentration: {deal_value/new_total:.0%} of portfolio"
            )

        return {
            "deal_name": deal.get("name", ""),
            "portfolio_before": {
                "total_value": round(total_value, 2),
                "total_debt": round(total_debt, 2),
                "leverage_ratio": leverage_before,
                "state_hhi": hhi_before,
                "asset_count": len(assets),
            },
            "portfolio_after": {
                "total_value": round(new_total, 2),
                "total_debt": round(new_debt, 2),
                "leverage_ratio": leverage_after,
                "state_hhi": hhi_after,
                "asset_count": len(assets) + 1,
            },
            "deltas": {
                "value_added": round(deal_value, 2),
                "leverage_change": round(leverage_after - leverage_before, 4),
                "hhi_change": round(hhi_after - hhi_before, 4),
                "state_exposure": state_exposure_after,
            },
            "warnings": warnings,
            "recommendation": (
                "CAUTION" if warnings else "OK"
            ),
        }


def _herfindahl(values: List[float], total: float) -> float:
    """Compute Herfindahl-Hirschman Index.

    HHI = sum of squared market shares.
    Range: 1/N (perfectly diversified) to 1.0 (fully concentrated).
    >0.25 is considered concentrated.
    """
    if total <= 0 or not values:
        return 0.0
    shares = [v / total for v in values if v > 0]
    return round(sum(s * s for s in shares), 6)
