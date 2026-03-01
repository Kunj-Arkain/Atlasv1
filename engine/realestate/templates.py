"""
engine.realestate.templates — Property Type Templates
=========================================================
Phase 6B: Default assumptions and scoring weights for each property type.

Each template defines:
  - Property type identifier
  - Default financial assumptions (cap rate, vacancy, expenses, etc.)
  - Stage-specific parameters
  - Scoring weights for decision engine
"""

from __future__ import annotations

from typing import Any, Dict, List


def default_property_templates() -> List[Dict[str, Any]]:
    """Return default property templates for common types."""
    return [
        {
            "name": "Retail Strip Center",
            "property_type": "retail_strip",
            "defaults": {
                "cap_rate": 0.075,
                "vacancy_rate": 0.08,
                "expense_ratio": 0.35,
                "rent_growth_annual": 0.02,
                "hold_period_years": 7,
                "exit_cap_rate": 0.080,
                "renovation_pct_of_price": 0.10,
                "target_irr": 0.15,
                "min_dscr": 1.25,
                "ltv_max": 0.75,
                "loan_rate": 0.065,
                "loan_term_years": 25,
                "loan_amort_years": 30,
                "gaming_eligible": True,
                "typical_terminal_count": 5,
            },
            "scoring_weights": {
                "financial_return": 0.30,
                "market_strength": 0.20,
                "feasibility": 0.15,
                "cost_risk": 0.15,
                "gaming_upside": 0.10,
                "debt_coverage": 0.10,
            },
        },
        {
            "name": "QSR / Fast Food",
            "property_type": "qsr",
            "defaults": {
                "cap_rate": 0.055,
                "vacancy_rate": 0.03,
                "expense_ratio": 0.15,  # NNN lease
                "rent_growth_annual": 0.015,
                "hold_period_years": 10,
                "exit_cap_rate": 0.060,
                "renovation_pct_of_price": 0.05,
                "target_irr": 0.10,
                "min_dscr": 1.30,
                "ltv_max": 0.70,
                "loan_rate": 0.060,
                "loan_term_years": 25,
                "loan_amort_years": 30,
                "gaming_eligible": True,
                "typical_terminal_count": 5,
            },
            "scoring_weights": {
                "financial_return": 0.30,
                "market_strength": 0.20,
                "feasibility": 0.15,
                "cost_risk": 0.10,
                "gaming_upside": 0.10,
                "debt_coverage": 0.15,
            },
        },
        {
            "name": "Gas Station / C-Store",
            "property_type": "gas_station",
            "defaults": {
                "cap_rate": 0.065,
                "vacancy_rate": 0.05,
                "expense_ratio": 0.25,
                "rent_growth_annual": 0.02,
                "hold_period_years": 7,
                "exit_cap_rate": 0.070,
                "renovation_pct_of_price": 0.08,
                "target_irr": 0.18,
                "min_dscr": 1.20,
                "ltv_max": 0.75,
                "loan_rate": 0.070,
                "loan_term_years": 20,
                "loan_amort_years": 25,
                "gaming_eligible": True,
                "typical_terminal_count": 5,
            },
            "scoring_weights": {
                "financial_return": 0.25,
                "market_strength": 0.15,
                "feasibility": 0.15,
                "cost_risk": 0.10,
                "gaming_upside": 0.20,
                "debt_coverage": 0.15,
            },
        },
        {
            "name": "Dollar Store",
            "property_type": "dollar",
            "defaults": {
                "cap_rate": 0.065,
                "vacancy_rate": 0.03,
                "expense_ratio": 0.15,
                "rent_growth_annual": 0.01,
                "hold_period_years": 10,
                "exit_cap_rate": 0.070,
                "renovation_pct_of_price": 0.03,
                "target_irr": 0.10,
                "min_dscr": 1.35,
                "ltv_max": 0.70,
                "loan_rate": 0.060,
                "loan_term_years": 25,
                "loan_amort_years": 30,
                "gaming_eligible": True,
                "typical_terminal_count": 5,
            },
            "scoring_weights": {
                "financial_return": 0.30,
                "market_strength": 0.20,
                "feasibility": 0.15,
                "cost_risk": 0.15,
                "gaming_upside": 0.10,
                "debt_coverage": 0.10,
            },
        },
        {
            "name": "Bin Store",
            "property_type": "bin_store",
            "defaults": {
                "cap_rate": 0.085,
                "vacancy_rate": 0.05,
                "expense_ratio": 0.30,
                "rent_growth_annual": 0.02,
                "hold_period_years": 5,
                "exit_cap_rate": 0.090,
                "renovation_pct_of_price": 0.12,
                "target_irr": 0.20,
                "min_dscr": 1.20,
                "ltv_max": 0.75,
                "loan_rate": 0.070,
                "loan_term_years": 20,
                "loan_amort_years": 25,
                "gaming_eligible": True,
                "typical_terminal_count": 5,
            },
            "scoring_weights": {
                "financial_return": 0.25,
                "market_strength": 0.20,
                "feasibility": 0.15,
                "cost_risk": 0.15,
                "gaming_upside": 0.15,
                "debt_coverage": 0.10,
            },
        },
        {
            "name": "Shopping Center",
            "property_type": "shopping_center",
            "defaults": {
                "cap_rate": 0.080,
                "vacancy_rate": 0.10,
                "expense_ratio": 0.40,
                "rent_growth_annual": 0.02,
                "hold_period_years": 7,
                "exit_cap_rate": 0.085,
                "renovation_pct_of_price": 0.15,
                "target_irr": 0.18,
                "min_dscr": 1.20,
                "ltv_max": 0.70,
                "loan_rate": 0.070,
                "loan_term_years": 20,
                "loan_amort_years": 25,
                "gaming_eligible": True,
                "typical_terminal_count": 5,
            },
            "scoring_weights": {
                "financial_return": 0.25,
                "market_strength": 0.25,
                "feasibility": 0.15,
                "cost_risk": 0.15,
                "gaming_upside": 0.10,
                "debt_coverage": 0.10,
            },
        },
    ]


def get_template_for_type(property_type: str) -> Dict[str, Any]:
    """Look up default template by property type. Returns retail_strip as fallback."""
    for t in default_property_templates():
        if t["property_type"] == property_type:
            return t
    return default_property_templates()[0]
