"""
engine.financial.tools — Micro Financial Tools Suite
======================================================
Phase 2: Deterministic, auditable financial calculators.

Every downstream pipeline depends on these:
  - EGM contract Monte Carlo (Phase 5) → IRR, NPV, amortization
  - RE Capital Filter (Phase 6) → DSCR, cap rate, sensitivity
  - Deal scoring → all of the above

Design principles:
  - Pure Python, zero external dependencies
  - Dataclass in → dataclass out (serializable, auditable)
  - Every computation is deterministic (same inputs = same outputs)
  - All results round to 2 decimal places for currency, 6 for rates
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# AMORTIZATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class AmortizationInput:
    principal: float            # Loan amount ($)
    annual_rate: float          # Annual interest rate (0.065 = 6.5%)
    term_months: int            # Loan term in months
    extra_monthly: float = 0.0  # Extra principal payment per month

    def validate(self):
        if self.principal <= 0:
            raise ValueError("Principal must be positive")
        if self.annual_rate < 0:
            raise ValueError("Rate cannot be negative")
        if self.term_months <= 0:
            raise ValueError("Term must be positive")
        if self.extra_monthly < 0:
            raise ValueError("Extra payment cannot be negative")


@dataclass
class AmortizationPayment:
    month: int
    payment: float
    principal_portion: float
    interest_portion: float
    extra_principal: float
    remaining_balance: float


@dataclass
class AmortizationOutput:
    monthly_payment: float
    total_interest: float
    total_paid: float
    actual_term_months: int     # May be shorter with extra payments
    schedule: List[AmortizationPayment] = field(default_factory=list)


def amortize(inp: AmortizationInput) -> AmortizationOutput:
    """Compute a full amortization schedule.

    Supports extra monthly payments which shorten the loan term.
    Uses standard mortgage math: PMT = P * [r(1+r)^n] / [(1+r)^n - 1]
    """
    inp.validate()

    monthly_rate = inp.annual_rate / 12.0
    n = inp.term_months

    # Calculate standard monthly payment
    if monthly_rate == 0:
        base_payment = inp.principal / n
    else:
        base_payment = inp.principal * (
            monthly_rate * (1 + monthly_rate) ** n
        ) / (
            (1 + monthly_rate) ** n - 1
        )

    base_payment = round(base_payment, 2)
    balance = inp.principal
    schedule = []
    total_interest = 0.0
    total_paid = 0.0
    month = 0

    while balance > 0 and month < n * 2:  # Safety cap at 2x term
        month += 1
        interest = round(balance * monthly_rate, 2)
        principal_portion = base_payment - interest

        # Apply extra payment
        extra = min(inp.extra_monthly, balance - principal_portion)
        extra = max(0, extra)
        principal_portion += extra

        # Final payment adjustment: if this is the last scheduled month
        # or the remaining balance is less than the base payment,
        # pay off the balance exactly.
        if principal_portion >= balance or month == n:
            principal_portion = balance
            extra = 0.0
            actual_payment = round(interest + balance, 2)
        else:
            actual_payment = base_payment + extra

        balance -= principal_portion
        balance = round(max(0, balance), 2)
        total_interest += interest
        total_paid += actual_payment

        schedule.append(AmortizationPayment(
            month=month,
            payment=round(actual_payment, 2),
            principal_portion=round(principal_portion - extra, 2),
            interest_portion=round(interest, 2),
            extra_principal=round(extra, 2),
            remaining_balance=balance,
        ))

    return AmortizationOutput(
        monthly_payment=base_payment,
        total_interest=round(total_interest, 2),
        total_paid=round(total_paid, 2),
        actual_term_months=month,
        schedule=schedule,
    )


# ═══════════════════════════════════════════════════════════════
# TIME VALUE OF MONEY (TVM)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TVMInput:
    """Provide any 4 of 5; the missing one (None) will be solved."""
    pv: Optional[float] = None    # Present value
    fv: Optional[float] = None    # Future value
    pmt: Optional[float] = None   # Payment per period
    rate: Optional[float] = None  # Rate per period (0.005 = 0.5%/period)
    nper: Optional[float] = None  # Number of periods

    def validate(self):
        nones = sum(1 for v in [self.pv, self.fv, self.pmt, self.rate, self.nper]
                     if v is None)
        if nones != 1:
            raise ValueError(
                f"Exactly one of pv/fv/pmt/rate/nper must be None (got {nones} Nones)"
            )


@dataclass
class TVMOutput:
    pv: float
    fv: float
    pmt: float
    rate: float
    nper: float
    solved_for: str  # Which variable was computed


def tvm_solve(inp: TVMInput) -> TVMOutput:
    """Solve for the missing TVM variable.

    Sign convention: outflows are negative, inflows are positive.
    Standard financial calculator convention.
    """
    inp.validate()

    pv = inp.pv
    fv = inp.fv
    pmt = inp.pmt
    rate = inp.rate
    nper = inp.nper

    if pv is None:
        # PV = -FV / (1+r)^n - PMT * [(1+r)^n - 1] / [r * (1+r)^n]
        solved_for = "pv"
        if rate == 0:
            pv = -(fv + pmt * nper)
        else:
            factor = (1 + rate) ** nper
            pv = -(fv / factor + pmt * (factor - 1) / (rate * factor))
        pv = round(pv, 2)

    elif fv is None:
        # FV = -PV * (1+r)^n - PMT * [(1+r)^n - 1] / r
        solved_for = "fv"
        if rate == 0:
            fv = -(pv + pmt * nper)
        else:
            factor = (1 + rate) ** nper
            fv = -(pv * factor + pmt * (factor - 1) / rate)
        fv = round(fv, 2)

    elif pmt is None:
        # PMT = [-PV * r * (1+r)^n - FV * r] / [(1+r)^n - 1]
        solved_for = "pmt"
        if rate == 0:
            pmt = -(pv + fv) / nper
        else:
            factor = (1 + rate) ** nper
            pmt = -(pv * rate * factor + fv * rate) / (factor - 1)
        pmt = round(pmt, 2)

    elif rate is None:
        # Solve numerically using Newton-Raphson
        solved_for = "rate"
        rate = _solve_rate(pv, fv, pmt, nper)
        rate = round(rate, 8)

    elif nper is None:
        # NPER = ln[(-FV*r + PMT) / (PV*r + PMT)] / ln(1 + r)
        solved_for = "nper"
        if rate == 0:
            nper = -(pv + fv) / pmt if pmt != 0 else 0
        else:
            numerator = -fv * rate + pmt
            denominator = pv * rate + pmt
            if denominator == 0 or numerator / denominator <= 0:
                raise ValueError("Cannot solve for nper with these inputs")
            nper = math.log(numerator / denominator) / math.log(1 + rate)
        nper = round(nper, 2)

    return TVMOutput(
        pv=pv, fv=fv, pmt=pmt, rate=rate, nper=nper,
        solved_for=solved_for,
    )


def _solve_rate(pv, fv, pmt, nper, tol=1e-10, max_iter=200) -> float:
    """Newton-Raphson solver for interest rate."""
    # Initial guess
    r = 0.05

    for _ in range(max_iter):
        # f(r) = PV*(1+r)^n + PMT*[(1+r)^n - 1]/r + FV = 0
        if abs(r) < 1e-14:
            # Near zero, use Taylor approximation
            f = pv + pmt * nper + fv
            df = pv * nper + pmt * nper * (nper - 1) / 2
        else:
            factor = (1 + r) ** nper
            f = pv * factor + pmt * (factor - 1) / r + fv
            # Derivative
            dfactor = nper * (1 + r) ** (nper - 1)
            df = pv * dfactor + pmt * (dfactor * r - (factor - 1)) / (r * r)

        if abs(df) < 1e-20:
            break

        r_new = r - f / df
        if abs(r_new - r) < tol:
            return r_new
        r = r_new

    return r


# ═══════════════════════════════════════════════════════════════
# IRR / NPV
# ═══════════════════════════════════════════════════════════════

@dataclass
class CashFlowInput:
    """Cash flows for IRR/NPV calculation."""
    cash_flows: List[float]         # Period 0, 1, 2, ... (negative = outflow)
    discount_rate: Optional[float] = None  # For NPV only (annual, e.g. 0.10)

    def validate(self):
        if len(self.cash_flows) < 2:
            raise ValueError("Need at least 2 cash flows")
        has_neg = any(cf < 0 for cf in self.cash_flows)
        has_pos = any(cf > 0 for cf in self.cash_flows)
        if not (has_neg and has_pos):
            raise ValueError("Cash flows must have both positive and negative values for IRR")


@dataclass
class IRRNPVOutput:
    irr: Optional[float]       # Internal rate of return (None if no convergence)
    npv: Optional[float]       # Net present value (None if no discount_rate given)
    payback_period: Optional[float]  # Periods until cumulative CF >= 0


def compute_irr_npv(inp: CashFlowInput) -> IRRNPVOutput:
    """Compute IRR, NPV, and payback period for a cash flow series."""
    inp.validate()
    cfs = inp.cash_flows

    # NPV
    npv = None
    if inp.discount_rate is not None:
        npv = round(_npv(cfs, inp.discount_rate), 2)

    # IRR via Newton-Raphson
    irr = _solve_irr(cfs)
    if irr is not None:
        irr = round(irr, 6)

    # Payback period
    payback = _payback_period(cfs)

    return IRRNPVOutput(irr=irr, npv=npv, payback_period=payback)


def _npv(cash_flows: List[float], rate: float) -> float:
    """Net present value."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))


def _solve_irr(cash_flows: List[float], tol=1e-10, max_iter=300) -> Optional[float]:
    """Newton-Raphson IRR solver."""
    # Initial guess based on simple return
    total_return = sum(cash_flows[1:])
    initial_inv = abs(cash_flows[0]) if cash_flows[0] != 0 else 1
    r = (total_return / initial_inv) / len(cash_flows) if initial_inv > 0 else 0.1

    # Clamp initial guess
    r = max(-0.5, min(r, 5.0))

    for _ in range(max_iter):
        f = sum(cf / (1 + r) ** t for t, cf in enumerate(cash_flows))
        df = sum(-t * cf / (1 + r) ** (t + 1) for t, cf in enumerate(cash_flows))

        if abs(df) < 1e-20:
            # Try bisection fallback
            return _solve_irr_bisection(cash_flows)

        r_new = r - f / df

        # Clamp to prevent divergence
        r_new = max(-0.99, min(r_new, 100.0))

        if abs(r_new - r) < tol:
            return r_new
        r = r_new

    # Fallback to bisection
    return _solve_irr_bisection(cash_flows)


def _solve_irr_bisection(cash_flows: List[float], tol=1e-8) -> Optional[float]:
    """Bisection method IRR solver (robust fallback)."""
    lo, hi = -0.99, 10.0

    f_lo = _npv(cash_flows, lo)
    f_hi = _npv(cash_flows, hi)

    if f_lo * f_hi > 0:
        return None  # No root in range

    for _ in range(500):
        mid = (lo + hi) / 2
        f_mid = _npv(cash_flows, mid)

        if abs(f_mid) < tol or (hi - lo) / 2 < tol:
            return mid

        if f_mid * f_lo < 0:
            hi = mid
        else:
            lo = mid
            f_lo = f_mid

    return (lo + hi) / 2


def _payback_period(cash_flows: List[float]) -> Optional[float]:
    """Simple payback period (periods until cumulative CF >= 0)."""
    cumulative = 0.0
    for t, cf in enumerate(cash_flows):
        prev_cumulative = cumulative
        cumulative += cf
        if cumulative >= 0 and t > 0:
            # Interpolate within the period
            if cf > 0:
                fraction = (0 - prev_cumulative) / cf
                return round(t - 1 + fraction, 2)
            return float(t)
    return None  # Never pays back


# ═══════════════════════════════════════════════════════════════
# DSCR — Debt Service Coverage Ratio
# ═══════════════════════════════════════════════════════════════

@dataclass
class DSCRInput:
    noi: float                  # Net Operating Income (annual)
    annual_debt_service: float  # Total annual debt payments (P&I)

    def validate(self):
        if self.annual_debt_service <= 0:
            raise ValueError("Annual debt service must be positive")


@dataclass
class DSCROutput:
    ratio: float
    noi: float
    annual_debt_service: float
    assessment: str  # 'strong', 'adequate', 'weak', 'critical'


def compute_dscr(inp: DSCRInput) -> DSCROutput:
    """Compute Debt Service Coverage Ratio.

    DSCR = NOI / Annual Debt Service
    - > 1.25 = Strong (most lenders comfortable)
    - 1.10 - 1.25 = Adequate
    - 1.00 - 1.10 = Weak (risky)
    - < 1.00 = Critical (cannot cover debt)
    """
    inp.validate()
    ratio = round(inp.noi / inp.annual_debt_service, 4)

    if ratio >= 1.25:
        assessment = "strong"
    elif ratio >= 1.10:
        assessment = "adequate"
    elif ratio >= 1.00:
        assessment = "weak"
    else:
        assessment = "critical"

    return DSCROutput(
        ratio=ratio,
        noi=inp.noi,
        annual_debt_service=inp.annual_debt_service,
        assessment=assessment,
    )


# ═══════════════════════════════════════════════════════════════
# CAP RATE ⇄ NOI ⇄ VALUE
# ═══════════════════════════════════════════════════════════════

@dataclass
class CapRateInput:
    """Provide any 2 of 3; the missing one (None) will be solved.

    cap_rate = NOI / value
    """
    cap_rate: Optional[float] = None   # e.g. 0.075 = 7.5%
    noi: Optional[float] = None        # Annual NOI ($)
    value: Optional[float] = None      # Property value ($)

    def validate(self):
        nones = sum(1 for v in [self.cap_rate, self.noi, self.value] if v is None)
        if nones != 1:
            raise ValueError(
                f"Exactly one of cap_rate/noi/value must be None (got {nones} Nones)"
            )


@dataclass
class CapRateOutput:
    cap_rate: float
    noi: float
    value: float
    solved_for: str


def solve_cap_rate(inp: CapRateInput) -> CapRateOutput:
    """Solve for the missing variable in cap_rate = NOI / value."""
    inp.validate()

    cap_rate = inp.cap_rate
    noi = inp.noi
    value = inp.value

    if cap_rate is None:
        if value == 0:
            raise ValueError("Value cannot be zero when solving for cap rate")
        cap_rate = noi / value
        solved_for = "cap_rate"
    elif noi is None:
        noi = cap_rate * value
        solved_for = "noi"
    else:
        if cap_rate == 0:
            raise ValueError("Cap rate cannot be zero when solving for value")
        value = noi / cap_rate
        solved_for = "value"

    return CapRateOutput(
        cap_rate=round(cap_rate, 6),
        noi=round(noi, 2),
        value=round(value, 2),
        solved_for=solved_for,
    )


# ═══════════════════════════════════════════════════════════════
# SENSITIVITY ANALYSIS
# ═══════════════════════════════════════════════════════════════

@dataclass
class SensitivityInput:
    """2D sensitivity matrix configuration.

    The compute_fn takes a dict of {variable_name: value} and returns a float.
    row_variable and col_variable define the two axes.
    """
    base_case: Dict[str, float]       # All variable values at base case
    row_variable: str                  # Variable name for rows
    row_values: List[float]           # Values to test on row axis
    col_variable: str                 # Variable name for columns
    col_values: List[float]           # Values to test on column axis
    compute_fn: Callable[[Dict[str, float]], float] = None  # Computation function
    output_label: str = "result"      # Label for the output metric

    def validate(self):
        if self.row_variable not in self.base_case:
            raise ValueError(f"Row variable '{self.row_variable}' not in base case")
        if self.col_variable not in self.base_case:
            raise ValueError(f"Col variable '{self.col_variable}' not in base case")
        if not self.row_values or not self.col_values:
            raise ValueError("Row and column values must be non-empty")
        if self.compute_fn is None:
            raise ValueError("compute_fn is required")


@dataclass
class SensitivityOutput:
    row_variable: str
    col_variable: str
    row_values: List[float]
    col_values: List[float]
    base_case_value: float
    matrix: List[List[float]]  # [row][col] = computed result
    output_label: str


def sensitivity_matrix(inp: SensitivityInput) -> SensitivityOutput:
    """Compute a 2D sensitivity matrix.

    Varies two inputs across their ranges while holding all other
    variables at their base case values. Returns a grid of results.
    """
    inp.validate()

    # Compute base case
    base_result = inp.compute_fn(inp.base_case)

    # Build matrix
    matrix = []
    for row_val in inp.row_values:
        row = []
        for col_val in inp.col_values:
            params = dict(inp.base_case)
            params[inp.row_variable] = row_val
            params[inp.col_variable] = col_val
            result = inp.compute_fn(params)
            row.append(round(result, 4))
        matrix.append(row)

    return SensitivityOutput(
        row_variable=inp.row_variable,
        col_variable=inp.col_variable,
        row_values=inp.row_values,
        col_values=inp.col_values,
        base_case_value=round(base_result, 4),
        matrix=matrix,
        output_label=inp.output_label,
    )


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════

# Simple mapping for the policy broker and ACP to reference
FINANCIAL_TOOLS = {
    "amortization": {
        "fn": amortize,
        "input_class": AmortizationInput,
        "output_class": AmortizationOutput,
        "description": "Compute loan amortization schedule with optional extra payments",
    },
    "tvm": {
        "fn": tvm_solve,
        "input_class": TVMInput,
        "output_class": TVMOutput,
        "description": "Solve for any missing TVM variable (PV/FV/PMT/rate/nper)",
    },
    "irr_npv": {
        "fn": compute_irr_npv,
        "input_class": CashFlowInput,
        "output_class": IRRNPVOutput,
        "description": "Compute IRR, NPV, and payback period from cash flows",
    },
    "dscr": {
        "fn": compute_dscr,
        "input_class": DSCRInput,
        "output_class": DSCROutput,
        "description": "Compute Debt Service Coverage Ratio with assessment",
    },
    "cap_rate": {
        "fn": solve_cap_rate,
        "input_class": CapRateInput,
        "output_class": CapRateOutput,
        "description": "Solve for cap rate, NOI, or value (given any 2 of 3)",
    },
    "sensitivity": {
        "fn": sensitivity_matrix,
        "input_class": SensitivityInput,
        "output_class": SensitivityOutput,
        "description": "2D sensitivity analysis across two variables",
    },
}


# ═══════════════════════════════════════════════════════════════
# TOOLKIT WRAPPER — Used by engine.brain.tools
# ═══════════════════════════════════════════════════════════════

class FinancialToolkit:
    """Convenience wrapper around individual financial functions.

    Used by ToolRegistry to register financial tools.
    """

    def amortize(self, principal: float = 0, annual_rate: float = 0.07,
                 months: int = 360, **kw) -> dict:
        inp = AmortizationInput(
            principal=principal, annual_rate=annual_rate if annual_rate < 1 else annual_rate / 100,
            term_months=months,
        )
        out = amortize(inp)
        return {
            "monthly_payment": out.monthly_payment,
            "total_interest": out.total_interest,
            "total_paid": out.total_paid,
            "schedule_length": len(out.schedule),
        }

    def irr(self, cash_flows: list = None, **kw) -> dict:
        if not cash_flows:
            return {"error": "cash_flows required"}
        inp = CashFlowInput(cash_flows=cash_flows)
        out = compute_irr_npv(inp)
        return {"irr": out.irr, "npv_at_10pct": out.npv}

    def dscr(self, noi: float = 0, annual_debt_service: float = 0, **kw) -> dict:
        if annual_debt_service <= 0:
            return {"dscr": 0, "status": "no_debt"}
        inp = DSCRInput(noi=noi, annual_debt_service=annual_debt_service)
        out = compute_dscr(inp)
        return {"dscr": out.ratio, "status": out.assessment, "noi": noi,
                "annual_debt_service": annual_debt_service}

    def cap_rate(self, noi: float = 0, value: float = 0, **kw) -> dict:
        if value <= 0:
            return {"cap_rate": 0}
        inp = CapRateInput(noi=noi, value=value)
        out = solve_cap_rate(inp)
        return {"cap_rate": out.cap_rate, "noi": noi, "value": value}

    def cash_on_cash(self, annual_cf: float = 0, equity_invested: float = 0, **kw) -> dict:
        if equity_invested <= 0:
            return {"cash_on_cash": 0}
        return {
            "cash_on_cash": round(annual_cf / equity_invested, 4),
            "annual_cf": annual_cf,
            "equity_invested": equity_invested,
        }
