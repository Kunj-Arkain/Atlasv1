"""
engine.contracts.templates — Contract Template System
========================================================
Phase 5A/B/C: Defines gaming contract structures,
validates terms against constraints, handles overrides.

Agreement types:
  - revenue_share: operator % / host % split of net win
  - flat_lease: fixed monthly payment to host
  - hybrid: base lease + revenue share above a threshold

Acquisition types:
  - cash: upfront purchase
  - financed: loan with terms (feeds Phase 2 amortization)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# CONTRACT TERM SCHEMAS
# ═══════════════════════════════════════════════════════════════

AGREEMENT_TYPES = {"revenue_share", "flat_lease", "hybrid"}
ACQUISITION_TYPES = {"cash", "financed"}

# Required fields per agreement type
REQUIRED_TERMS = {
    "revenue_share": {
        "operator_split",      # 0.0–1.0 (operator's share of net win)
        "host_split",          # 0.0–1.0 (host's share of net win)
        "contract_months",     # duration in months
    },
    "flat_lease": {
        "monthly_lease",       # $ per month paid to host
        "contract_months",
    },
    "hybrid": {
        "base_lease",          # $ per month base
        "threshold",           # $ net win above which revenue share kicks in
        "operator_split",      # split on amount above threshold
        "host_split",
        "contract_months",
    },
}

# Default constraints (can be overridden per template)
DEFAULT_CONSTRAINTS = {
    "operator_split": {"min": 0.20, "max": 0.80},
    "host_split": {"min": 0.20, "max": 0.80},
    "monthly_lease": {"min": 500, "max": 25000},
    "base_lease": {"min": 500, "max": 15000},
    "threshold": {"min": 0, "max": 500000},
    "contract_months": {"min": 12, "max": 120},
    "terminal_count": {"min": 1, "max": 10},
    "acquisition_cost": {"min": 0, "max": 5000000},
    "annual_rate": {"min": 0, "max": 0.25},
    "loan_term_months": {"min": 12, "max": 84},
}


# ═══════════════════════════════════════════════════════════════
# DEFAULT TEMPLATES
# ═══════════════════════════════════════════════════════════════

def default_templates() -> List[Dict]:
    """Seed default contract templates."""
    return [
        {
            "name": "IL Standard Revenue Share",
            "agreement_type": "revenue_share",
            "acquisition_type": "cash",
            "terms": {
                "operator_split": 0.65,
                "host_split": 0.35,
                "contract_months": 60,
                "terminal_count": 5,
            },
            "constraints": {
                "operator_split": {"min": 0.50, "max": 0.75},
                "host_split": {"min": 0.25, "max": 0.50},
                "contract_months": {"min": 36, "max": 84},
            },
            "state_applicability": "IL",
        },
        {
            "name": "IL Flat Lease",
            "agreement_type": "flat_lease",
            "acquisition_type": "cash",
            "terms": {
                "monthly_lease": 2000,
                "contract_months": 60,
                "terminal_count": 5,
            },
            "constraints": {
                "monthly_lease": {"min": 1000, "max": 10000},
                "contract_months": {"min": 36, "max": 84},
            },
            "state_applicability": "IL",
        },
        {
            "name": "IL Hybrid",
            "agreement_type": "hybrid",
            "acquisition_type": "cash",
            "terms": {
                "base_lease": 1500,
                "threshold": 20000,
                "operator_split": 0.60,
                "host_split": 0.40,
                "contract_months": 60,
                "terminal_count": 5,
            },
            "constraints": {
                "base_lease": {"min": 500, "max": 5000},
                "threshold": {"min": 5000, "max": 50000},
            },
            "state_applicability": "IL",
        },
        {
            "name": "Financed Acquisition",
            "agreement_type": "revenue_share",
            "acquisition_type": "financed",
            "terms": {
                "operator_split": 0.65,
                "host_split": 0.35,
                "contract_months": 60,
                "terminal_count": 5,
                "acquisition_cost": 150000,
                "down_payment_pct": 0.20,
                "annual_rate": 0.085,
                "loan_term_months": 48,
            },
            "constraints": {
                "down_payment_pct": {"min": 0.10, "max": 0.50},
                "annual_rate": {"min": 0.04, "max": 0.15},
            },
            "state_applicability": "IL,NV,PA,CO",
        },
    ]


# ═══════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class ValidationError:
    field: str
    message: str
    value: Any = None


def validate_terms(
    agreement_type: str,
    terms: Dict[str, Any],
    constraints: Optional[Dict[str, Dict]] = None,
) -> List[ValidationError]:
    """Validate contract terms against schema and constraints.

    Returns list of errors (empty = valid).
    """
    errors = []

    if agreement_type not in AGREEMENT_TYPES:
        errors.append(ValidationError(
            "agreement_type", f"Invalid type: {agreement_type}. "
            f"Must be one of {AGREEMENT_TYPES}", agreement_type
        ))
        return errors

    # Check required fields
    required = REQUIRED_TERMS[agreement_type]
    for field_name in required:
        if field_name not in terms:
            errors.append(ValidationError(
                field_name, f"Required field missing for {agreement_type}",
            ))

    # Check split adds to 1.0
    if agreement_type in ("revenue_share", "hybrid"):
        op_split = terms.get("operator_split", 0)
        host_split = terms.get("host_split", 0)
        total = op_split + host_split
        if abs(total - 1.0) > 0.001:
            errors.append(ValidationError(
                "operator_split+host_split",
                f"Splits must sum to 1.0, got {total}",
                total,
            ))

    # Check constraints
    merged_constraints = dict(DEFAULT_CONSTRAINTS)
    if constraints:
        merged_constraints.update(constraints)

    for field_name, value in terms.items():
        if not isinstance(value, (int, float)):
            continue
        if field_name in merged_constraints:
            c = merged_constraints[field_name]
            if "min" in c and value < c["min"]:
                errors.append(ValidationError(
                    field_name,
                    f"Below minimum {c['min']}",
                    value,
                ))
            if "max" in c and value > c["max"]:
                errors.append(ValidationError(
                    field_name,
                    f"Above maximum {c['max']}",
                    value,
                ))

    return errors


def apply_overrides(
    base_terms: Dict[str, Any],
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge overrides into base terms.

    Returns new dict (does not mutate base_terms).
    """
    merged = dict(base_terms)
    merged.update(overrides)
    return merged


def compute_monthly_operator_cash(
    agreement_type: str,
    terms: Dict[str, Any],
    net_win: float,
) -> float:
    """Compute the operator's monthly cash flow from net win.

    Args:
        agreement_type: 'revenue_share', 'flat_lease', or 'hybrid'
        terms: Contract terms dict
        net_win: Monthly net terminal income

    Returns:
        Operator's monthly cash (after host payment)
    """
    if agreement_type == "revenue_share":
        return net_win * terms.get("operator_split", 0.65)

    elif agreement_type == "flat_lease":
        return net_win - terms.get("monthly_lease", 0)

    elif agreement_type == "hybrid":
        base_lease = terms.get("base_lease", 0)
        threshold = terms.get("threshold", 0)
        if net_win <= threshold:
            return net_win - base_lease
        else:
            above = net_win - threshold
            op_share = above * terms.get("operator_split", 0.60)
            host_share_above = above * terms.get("host_split", 0.40)
            return (threshold - base_lease) + op_share

    return 0.0


def compute_monthly_debt_service(terms: Dict[str, Any]) -> float:
    """Compute monthly debt service for financed acquisitions.

    Uses standard mortgage math (same as Phase 2 amortization).
    """
    acq_cost = terms.get("acquisition_cost", 0)
    down_pct = terms.get("down_payment_pct", 0.20)
    annual_rate = terms.get("annual_rate", 0)
    loan_months = terms.get("loan_term_months", 48)

    principal = acq_cost * (1 - down_pct)
    if principal <= 0 or loan_months <= 0:
        return 0.0

    if annual_rate <= 0:
        return round(principal / loan_months, 2)

    monthly_rate = annual_rate / 12
    pmt = principal * (monthly_rate * (1 + monthly_rate) ** loan_months) / (
        (1 + monthly_rate) ** loan_months - 1
    )
    return round(pmt, 2)
