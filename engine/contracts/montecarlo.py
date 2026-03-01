"""
engine.contracts.montecarlo — Monte Carlo Simulation Engine
===============================================================
Phase 5D: Stochastic simulation of gaming contract outcomes.

Pipeline:
  1. Sample coin_in from log-normal distribution (fit to p10/p50/p90)
  2. Sample hold_pct from beta distribution (fit to p10/p50/p90)
  3. Compute net_win = coin_in × hold_pct per scenario
  4. Apply contract logic (revenue share / lease / hybrid)
  5. Subtract debt service for financed acquisitions
  6. Build cash flow series per scenario
  7. Compute IRR per scenario
  8. Aggregate: quantiles, downside risk, guardrails

Zero external dependencies — uses stdlib random + math.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.contracts.templates import (
    compute_monthly_operator_cash,
    compute_monthly_debt_service,
)


# ═══════════════════════════════════════════════════════════════
# DISTRIBUTION FITTING (from quantile predictions)
# ═══════════════════════════════════════════════════════════════

def _fit_lognormal(p10: float, p50: float, p90: float) -> Tuple[float, float]:
    """Fit log-normal parameters (mu, sigma) from quantiles.

    p50 of log-normal = exp(mu), so mu = ln(p50)
    Width of band determines sigma.
    """
    if p50 <= 0:
        return 0.0, 0.1
    mu = math.log(max(p50, 1))
    # sigma from IQR: ln(p90/p10) ≈ 2 * 1.282 * sigma
    if p10 > 0 and p90 > p10:
        sigma = math.log(p90 / p10) / (2 * 1.282)
    else:
        sigma = 0.3  # default moderate uncertainty
    return mu, max(sigma, 0.01)


def _sample_lognormal(mu: float, sigma: float, rng: random.Random) -> float:
    """Sample from log-normal distribution."""
    return math.exp(rng.gauss(mu, sigma))


def _fit_beta_from_quantiles(
    p10: float, p50: float, p90: float,
) -> Tuple[float, float]:
    """Fit beta distribution parameters for hold_pct (0–1 bounded).

    Approximation: match mean and variance to quantiles.
    """
    mean = p50
    if mean <= 0 or mean >= 1:
        mean = 0.26  # default hold%

    # Approximate variance from IQR
    if p90 > p10:
        std = (p90 - p10) / (2 * 1.282)
    else:
        std = 0.03

    variance = std ** 2
    # Beta params from mean, variance
    if variance <= 0 or variance >= mean * (1 - mean):
        variance = mean * (1 - mean) * 0.1  # cap at 10% of max

    common = mean * (1 - mean) / variance - 1
    alpha = max(mean * common, 1.01)
    beta = max((1 - mean) * common, 1.01)
    return alpha, beta


def _sample_beta(alpha: float, beta_param: float, rng: random.Random) -> float:
    """Sample from beta distribution using Jöhnk's algorithm."""
    return rng.betavariate(alpha, beta_param)


# ═══════════════════════════════════════════════════════════════
# IRR SOLVER (lightweight, same algorithm as Phase 2)
# ═══════════════════════════════════════════════════════════════

def _npv(cash_flows: List[float], rate: float) -> float:
    total = 0.0
    for i, cf in enumerate(cash_flows):
        total += cf / (1 + rate) ** i
    return total


def _solve_irr(cash_flows: List[float], tol: float = 1e-6, max_iter: int = 100) -> Optional[float]:
    """Newton-Raphson IRR solver with bisection fallback."""
    if not cash_flows or all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        return None

    # Bisection
    lo, hi = -0.5, 5.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        val = _npv(cash_flows, mid)
        if abs(val) < tol:
            return round(mid, 6)
        if val > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 6)


# ═══════════════════════════════════════════════════════════════
# MONTE CARLO SIMULATOR
# ═══════════════════════════════════════════════════════════════

@dataclass
class SimulationInputs:
    """Inputs for a Monte Carlo simulation."""
    # Prediction quantiles (from Phase 4 or manual)
    coin_in_p10: float = 50000
    coin_in_p50: float = 80000
    coin_in_p90: float = 120000
    hold_pct_p10: float = 0.22
    hold_pct_p50: float = 0.26
    hold_pct_p90: float = 0.31

    # Contract terms
    agreement_type: str = "revenue_share"
    operator_split: float = 0.65
    host_split: float = 0.35
    monthly_lease: float = 2000
    base_lease: float = 1500
    threshold: float = 20000
    contract_months: int = 60
    terminal_count: int = 5

    # Acquisition
    acquisition_type: str = "cash"
    acquisition_cost: float = 150000
    down_payment_pct: float = 0.20
    annual_rate: float = 0.085
    loan_term_months: int = 48

    # Simulation config
    num_simulations: int = 10000
    seed: Optional[int] = None


@dataclass
class SimulationResult:
    """Output from Monte Carlo simulation."""
    # IRR distribution
    irr_p10: float = 0.0
    irr_p25: float = 0.0
    irr_p50: float = 0.0
    irr_p75: float = 0.0
    irr_p90: float = 0.0
    irr_mean: float = 0.0

    # Net win distribution (monthly)
    net_win_p10: float = 0.0
    net_win_p50: float = 0.0
    net_win_p90: float = 0.0

    # Operator cash flow distribution (monthly, after host payment + debt)
    operator_cf_p10: float = 0.0
    operator_cf_p50: float = 0.0
    operator_cf_p90: float = 0.0

    # Risk metrics
    prob_negative_irr: float = 0.0
    prob_below_10pct: float = 0.0
    prob_below_20pct: float = 0.0
    max_drawdown_p90: float = 0.0

    # Guardrails
    breakeven_net_win: float = 0.0  # Monthly NW needed for IRR=0
    target_net_win_20pct: float = 0.0  # NW needed for 20% IRR

    # Metadata
    num_simulations: int = 0
    valid_simulations: int = 0
    execution_ms: int = 0


def run_simulation(inputs: SimulationInputs) -> SimulationResult:
    """Run Monte Carlo simulation for a gaming contract.

    Generates `num_simulations` scenarios, each with sampled
    coin_in and hold_pct, applied to the contract structure.

    Returns aggregated quantile statistics.
    """
    start = time.perf_counter()
    rng = random.Random(inputs.seed)

    # Fit distributions
    ci_mu, ci_sigma = _fit_lognormal(
        inputs.coin_in_p10, inputs.coin_in_p50, inputs.coin_in_p90
    )
    hp_alpha, hp_beta = _fit_beta_from_quantiles(
        inputs.hold_pct_p10, inputs.hold_pct_p50, inputs.hold_pct_p90
    )

    # Compute initial outlay
    if inputs.acquisition_type == "financed":
        initial_outlay = inputs.acquisition_cost * inputs.down_payment_pct
        monthly_debt = compute_monthly_debt_service({
            "acquisition_cost": inputs.acquisition_cost,
            "down_payment_pct": inputs.down_payment_pct,
            "annual_rate": inputs.annual_rate,
            "loan_term_months": inputs.loan_term_months,
        })
    else:
        initial_outlay = inputs.acquisition_cost
        monthly_debt = 0.0

    # Build contract terms dict
    terms = {
        "operator_split": inputs.operator_split,
        "host_split": inputs.host_split,
        "monthly_lease": inputs.monthly_lease,
        "base_lease": inputs.base_lease,
        "threshold": inputs.threshold,
    }

    # Run scenarios
    irrs: List[float] = []
    net_wins: List[float] = []
    operator_cfs: List[float] = []

    for _ in range(inputs.num_simulations):
        # Sample performance
        coin_in = _sample_lognormal(ci_mu, ci_sigma, rng)
        hold_pct = _sample_beta(hp_alpha, hp_beta, rng)
        monthly_nw = coin_in * hold_pct

        # Operator monthly cash before debt
        op_cash = compute_monthly_operator_cash(
            inputs.agreement_type, terms, monthly_nw,
        )

        # Subtract debt service (for financed months only)
        if monthly_debt > 0:
            debt_months = min(inputs.loan_term_months, inputs.contract_months)
            cash_flows = [-initial_outlay]
            for m in range(1, inputs.contract_months + 1):
                if m <= debt_months:
                    cash_flows.append(op_cash - monthly_debt)
                else:
                    cash_flows.append(op_cash)
        else:
            cash_flows = [-initial_outlay]
            cash_flows.extend([op_cash] * inputs.contract_months)

        # Annualize for IRR (group into 12-month periods)
        annual_cfs = _annualize_cash_flows(cash_flows)
        irr = _solve_irr(annual_cfs) if len(annual_cfs) >= 2 else None

        if irr is not None:
            irrs.append(irr)
        net_wins.append(monthly_nw)
        operator_cfs.append(op_cash - (monthly_debt if monthly_debt > 0 else 0))

    # Aggregate results
    result = SimulationResult()
    result.num_simulations = inputs.num_simulations
    result.valid_simulations = len(irrs)

    if irrs:
        irrs_sorted = sorted(irrs)
        result.irr_p10 = _quantile(irrs_sorted, 0.10)
        result.irr_p25 = _quantile(irrs_sorted, 0.25)
        result.irr_p50 = _quantile(irrs_sorted, 0.50)
        result.irr_p75 = _quantile(irrs_sorted, 0.75)
        result.irr_p90 = _quantile(irrs_sorted, 0.90)
        result.irr_mean = round(sum(irrs) / len(irrs), 6)

        result.prob_negative_irr = round(
            sum(1 for x in irrs if x < 0) / len(irrs), 4
        )
        result.prob_below_10pct = round(
            sum(1 for x in irrs if x < 0.10) / len(irrs), 4
        )
        result.prob_below_20pct = round(
            sum(1 for x in irrs if x < 0.20) / len(irrs), 4
        )

    if net_wins:
        nw_sorted = sorted(net_wins)
        result.net_win_p10 = round(_quantile(nw_sorted, 0.10), 2)
        result.net_win_p50 = round(_quantile(nw_sorted, 0.50), 2)
        result.net_win_p90 = round(_quantile(nw_sorted, 0.90), 2)

    if operator_cfs:
        cf_sorted = sorted(operator_cfs)
        result.operator_cf_p10 = round(_quantile(cf_sorted, 0.10), 2)
        result.operator_cf_p50 = round(_quantile(cf_sorted, 0.50), 2)
        result.operator_cf_p90 = round(_quantile(cf_sorted, 0.90), 2)

    # Guardrails: find breakeven and target net_win
    result.breakeven_net_win = _find_breakeven_nw(
        inputs, terms, initial_outlay, monthly_debt, 0.0
    )
    result.target_net_win_20pct = _find_breakeven_nw(
        inputs, terms, initial_outlay, monthly_debt, 0.20
    )

    result.execution_ms = int((time.perf_counter() - start) * 1000)

    return result


def compare_structures(
    base_inputs: SimulationInputs,
    structures: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compare multiple contract structures on the same deal.

    Each structure overrides agreement_type and relevant terms.

    Returns ranked list by median IRR.
    """
    results = []
    for struct in structures:
        inp = SimulationInputs(
            coin_in_p10=base_inputs.coin_in_p10,
            coin_in_p50=base_inputs.coin_in_p50,
            coin_in_p90=base_inputs.coin_in_p90,
            hold_pct_p10=base_inputs.hold_pct_p10,
            hold_pct_p50=base_inputs.hold_pct_p50,
            hold_pct_p90=base_inputs.hold_pct_p90,
            acquisition_cost=base_inputs.acquisition_cost,
            acquisition_type=base_inputs.acquisition_type,
            down_payment_pct=base_inputs.down_payment_pct,
            annual_rate=base_inputs.annual_rate,
            loan_term_months=base_inputs.loan_term_months,
            terminal_count=base_inputs.terminal_count,
            num_simulations=base_inputs.num_simulations,
            seed=base_inputs.seed,
            **{k: v for k, v in struct.items()
               if hasattr(SimulationInputs, k)},
        )
        sim_result = run_simulation(inp)
        results.append({
            "structure": struct,
            "irr_p50": sim_result.irr_p50,
            "irr_p10": sim_result.irr_p10,
            "irr_p90": sim_result.irr_p90,
            "prob_negative": sim_result.prob_negative_irr,
            "operator_cf_p50": sim_result.operator_cf_p50,
            "breakeven_nw": sim_result.breakeven_net_win,
            "execution_ms": sim_result.execution_ms,
        })

    results.sort(key=lambda r: r["irr_p50"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _annualize_cash_flows(monthly: List[float]) -> List[float]:
    """Convert monthly cash flows to annual for IRR calculation."""
    if not monthly:
        return []
    annual = [monthly[0]]  # Initial outlay
    year_sum = 0.0
    for i, cf in enumerate(monthly[1:], 1):
        year_sum += cf
        if i % 12 == 0:
            annual.append(year_sum)
            year_sum = 0.0
    if year_sum != 0.0:
        annual.append(year_sum)
    return annual


def _quantile(sorted_values: List[float], q: float) -> float:
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


def _find_breakeven_nw(
    inputs: SimulationInputs,
    terms: Dict,
    initial_outlay: float,
    monthly_debt: float,
    target_irr: float,
) -> float:
    """Binary search for monthly net_win needed to achieve target IRR."""
    lo, hi = 0.0, 1_000_000.0
    for _ in range(50):
        mid = (lo + hi) / 2
        op_cash = compute_monthly_operator_cash(
            inputs.agreement_type, terms, mid,
        )
        if monthly_debt > 0:
            debt_months = min(inputs.loan_term_months, inputs.contract_months)
            cfs = [-initial_outlay]
            for m in range(1, inputs.contract_months + 1):
                if m <= debt_months:
                    cfs.append(op_cash - monthly_debt)
                else:
                    cfs.append(op_cash)
        else:
            cfs = [-initial_outlay] + [op_cash] * inputs.contract_months

        annual = _annualize_cash_flows(cfs)
        irr = _solve_irr(annual)
        if irr is None:
            lo = mid
        elif irr < target_irr:
            lo = mid
        else:
            hi = mid

    return round((lo + hi) / 2, 2)
