"""
engine.strategic.templates — Scenario Templates & Weight Presets
===================================================================
Pre-configured templates for common strategic analysis types.
"""

from __future__ import annotations
from typing import Any, Dict, List


def default_scenario_templates() -> List[Dict[str, Any]]:
    """Return default scenario analysis templates."""
    return [
        {
            "name": "Acquisition Analysis",
            "template_type": "acquisition",
            "description": "Evaluate a potential acquisition target",
            "default_objectives": [
                "Achieve target IRR within hold period",
                "Maintain portfolio diversification",
                "Minimize integration risk",
            ],
            "default_constraints": [
                "Maximum purchase price within budget",
                "Financing must be secured",
                "Due diligence complete before closing",
            ],
            "weight_preset": {
                "financial_return": 0.30,
                "market_position": 0.20,
                "execution_risk": 0.20,
                "strategic_fit": 0.15,
                "downside_protection": 0.15,
            },
            "stage_routes": {
                "compression": "cheap_structured",
                "decision_prep": "cheap_structured",
                "scenarios": "strategic_deep",
                "patterns": "strategic_deep",
                "synthesis": "strategic_deep",
            },
        },
        {
            "name": "Market Expansion",
            "template_type": "expansion",
            "description": "Evaluate entry into a new market or geography",
            "default_objectives": [
                "Establish market presence within timeline",
                "Achieve breakeven within 18 months",
                "Build local operational capability",
            ],
            "default_constraints": [
                "Capital budget fixed",
                "Regulatory compliance required",
                "Existing operations must not be disrupted",
            ],
            "weight_preset": {
                "market_opportunity": 0.25,
                "execution_feasibility": 0.25,
                "regulatory_risk": 0.20,
                "financial_viability": 0.20,
                "strategic_alignment": 0.10,
            },
            "stage_routes": {
                "compression": "cheap_structured",
                "decision_prep": "strategic_fast",
                "scenarios": "strategic_deep",
                "patterns": "strategic_deep",
                "synthesis": "strategic_deep",
            },
        },
        {
            "name": "Partnership / JV Evaluation",
            "template_type": "partnership",
            "description": "Evaluate a potential partnership or joint venture",
            "default_objectives": [
                "Create mutual value for both parties",
                "Maintain operational control where critical",
                "Clear governance and exit provisions",
            ],
            "default_constraints": [
                "Partner must pass due diligence",
                "Revenue share must be sustainable",
                "IP and data ownership clearly defined",
            ],
            "weight_preset": {
                "partner_quality": 0.25,
                "deal_structure": 0.25,
                "strategic_value": 0.20,
                "execution_risk": 0.15,
                "exit_optionality": 0.15,
            },
            "stage_routes": {
                "compression": "cheap_structured",
                "decision_prep": "cheap_structured",
                "scenarios": "strategic_deep",
                "patterns": "strategic_deep",
                "synthesis": "strategic_deep",
            },
        },
        {
            "name": "Gaming Expansion",
            "template_type": "gaming",
            "description": "Evaluate gaming terminal expansion or new venue",
            "default_objectives": [
                "Maximize net terminal income",
                "Achieve target ROI on terminal investment",
                "Secure favorable operator agreement",
            ],
            "default_constraints": [
                "State regulatory limits on terminal count",
                "Municipal gaming ordinance compliance",
                "Operator agreement terms within policy",
            ],
            "weight_preset": {
                "revenue_potential": 0.30,
                "regulatory_compliance": 0.20,
                "operator_terms": 0.20,
                "location_quality": 0.15,
                "portfolio_fit": 0.15,
            },
            "stage_routes": {
                "compression": "cheap_structured",
                "decision_prep": "cheap_structured",
                "scenarios": "strategic_deep",
                "patterns": "strategic_deep",
                "synthesis": "strategic_deep",
            },
        },
        {
            "name": "General Strategic Decision",
            "template_type": "general",
            "description": "Open-ended strategic analysis for any decision",
            "default_objectives": [],
            "default_constraints": [],
            "weight_preset": {
                "value_creation": 0.25,
                "risk_adjusted_return": 0.25,
                "execution_feasibility": 0.20,
                "strategic_alignment": 0.15,
                "optionality": 0.15,
            },
            "stage_routes": {
                "compression": "cheap_structured",
                "decision_prep": "strategic_fast",
                "scenarios": "strategic_deep",
                "patterns": "strategic_deep",
                "synthesis": "strategic_deep",
            },
        },
    ]


def get_template(template_type: str) -> Dict[str, Any]:
    """Look up template by type. Fallback to general."""
    for t in default_scenario_templates():
        if t["template_type"] == template_type:
            return t
    return default_scenario_templates()[-1]  # general
