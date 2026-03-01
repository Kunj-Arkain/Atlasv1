"""
tests/test_phase2_financial.py — Financial Tools Tests
========================================================
Known-answer test vectors verified against Excel / HP 12C.

Run: pytest tests/test_phase2_financial.py -v
"""

import math
import pytest

from engine.financial.tools import (
    amortize, AmortizationInput,
    tvm_solve, TVMInput,
    compute_irr_npv, CashFlowInput,
    compute_dscr, DSCRInput,
    solve_cap_rate, CapRateInput,
    sensitivity_matrix, SensitivityInput,
)
from engine.financial.serialization import to_dict, format_amortization_summary


# ═══════════════════════════════════════════════════════════════
# AMORTIZATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestAmortization:
    """Test vectors verified against standard mortgage calculators."""

    def test_standard_30yr_mortgage(self):
        """$250K at 6.5% for 30 years → ~$1,580.17/month."""
        result = amortize(AmortizationInput(
            principal=250_000, annual_rate=0.065, term_months=360
        ))
        assert result.monthly_payment == 1580.17
        assert result.actual_term_months == 360
        # Total interest on a 30yr mortgage is roughly 1.27x principal
        assert 310_000 < result.total_interest < 320_000

    def test_standard_15yr_mortgage(self):
        """$200K at 5.0% for 15 years → ~$1,581.59/month."""
        result = amortize(AmortizationInput(
            principal=200_000, annual_rate=0.05, term_months=180
        ))
        assert result.monthly_payment == 1581.59
        assert result.actual_term_months == 180
        assert 84_000 < result.total_interest < 86_000

    def test_extra_payments_shorten_term(self):
        """Extra $200/month should shorten a 30yr to roughly 23 years."""
        result = amortize(AmortizationInput(
            principal=250_000, annual_rate=0.065, term_months=360,
            extra_monthly=200,
        ))
        assert result.actual_term_months < 360
        assert result.actual_term_months < 290  # Should save ~6+ years
        assert result.total_interest < 310_000  # Less interest than no-extra

    def test_zero_rate_loan(self):
        """0% interest → equal principal payments."""
        result = amortize(AmortizationInput(
            principal=12_000, annual_rate=0.0, term_months=12
        ))
        assert result.monthly_payment == 1000.00
        assert result.total_interest == 0.0
        assert result.actual_term_months == 12

    def test_schedule_balance_reaches_zero(self):
        """Final balance should always be 0."""
        result = amortize(AmortizationInput(
            principal=100_000, annual_rate=0.07, term_months=120
        ))
        assert result.schedule[-1].remaining_balance == 0.0

    def test_first_payment_breakdown(self):
        """First payment: mostly interest on a standard loan."""
        result = amortize(AmortizationInput(
            principal=300_000, annual_rate=0.06, term_months=360
        ))
        first = result.schedule[0]
        # First month interest = 300K * 0.06/12 = $1,500
        assert first.interest_portion == 1500.00
        assert first.principal_portion < first.interest_portion

    def test_validation_negative_principal(self):
        with pytest.raises(ValueError, match="Principal must be positive"):
            amortize(AmortizationInput(
                principal=-100, annual_rate=0.05, term_months=12
            ))

    def test_validation_negative_rate(self):
        with pytest.raises(ValueError, match="Rate cannot be negative"):
            amortize(AmortizationInput(
                principal=100_000, annual_rate=-0.05, term_months=12
            ))

    def test_small_loan(self):
        """$1,000 at 10% for 12 months → ~$87.92/month."""
        result = amortize(AmortizationInput(
            principal=1000, annual_rate=0.10, term_months=12
        ))
        assert abs(result.monthly_payment - 87.92) <= 0.01
        assert result.actual_term_months == 12


# ═══════════════════════════════════════════════════════════════
# TVM TESTS
# ═══════════════════════════════════════════════════════════════

class TestTVM:
    """Time Value of Money — verified against HP 12C / Excel."""

    def test_solve_fv(self):
        """$10K invested at 0.5%/month for 60 months → FV."""
        result = tvm_solve(TVMInput(
            pv=-10_000, pmt=0, rate=0.005, nper=60, fv=None
        ))
        assert result.solved_for == "fv"
        # FV = 10000 * (1.005)^60 ≈ 13,488.50
        assert abs(result.fv - 13_488.50) < 5.0

    def test_solve_pv(self):
        """How much to invest now to get $50K in 10 years at 6%/yr?"""
        result = tvm_solve(TVMInput(
            fv=-50_000, pmt=0, rate=0.06, nper=10, pv=None
        ))
        assert result.solved_for == "pv"
        # PV = 50000 / (1.06)^10 ≈ 27,919.74
        assert abs(result.pv - 27_919.74) < 5.0

    def test_solve_pmt(self):
        """$200K loan at 0.5%/month for 360 months → monthly payment."""
        result = tvm_solve(TVMInput(
            pv=200_000, fv=0, rate=0.005, nper=360, pmt=None
        ))
        assert result.solved_for == "pmt"
        # PMT ≈ -$1,199.10
        assert abs(result.pmt - (-1199.10)) < 1.0

    def test_solve_nper(self):
        """$10K at 0.5%/month with $200/month → how many periods to reach $0?"""
        result = tvm_solve(TVMInput(
            pv=10_000, fv=0, pmt=-200, rate=0.005, nper=None
        ))
        assert result.solved_for == "nper"
        assert result.nper > 50
        assert result.nper < 70

    def test_solve_rate(self):
        """Known: PV=-10K, FV=15K, PMT=0, NPER=60 → solve rate."""
        result = tvm_solve(TVMInput(
            pv=-10_000, fv=15_000, pmt=0, nper=60, rate=None
        ))
        assert result.solved_for == "rate"
        # (15000/10000)^(1/60) - 1 ≈ 0.006785/period
        assert abs(result.rate - 0.006785) < 0.001

    def test_zero_rate_fv(self):
        """At 0% rate, FV = -(PV + PMT*NPER)."""
        result = tvm_solve(TVMInput(
            pv=-1000, pmt=-100, rate=0, nper=10, fv=None
        ))
        assert result.fv == 2000.0

    def test_validation_wrong_nones(self):
        with pytest.raises(ValueError, match="Exactly one"):
            tvm_solve(TVMInput(pv=None, fv=None, pmt=100, rate=0.05, nper=10))

    def test_savings_accumulation(self):
        """$500/month for 20 years at 7%/yr → how much saved?"""
        monthly_rate = 0.07 / 12
        result = tvm_solve(TVMInput(
            pv=0, pmt=-500, rate=monthly_rate, nper=240, fv=None
        ))
        # Should accumulate ~$260K
        assert result.fv > 250_000
        assert result.fv < 270_000


# ═══════════════════════════════════════════════════════════════
# IRR / NPV TESTS
# ═══════════════════════════════════════════════════════════════

class TestIRRNPV:
    """IRR/NPV verified against Excel IRR() and NPV()."""

    def test_simple_irr(self):
        """Classic: invest $100K, get $30K/yr for 5 years."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-100_000, 30_000, 30_000, 30_000, 30_000, 30_000]
        ))
        # Excel IRR = ~15.24%
        assert result.irr is not None
        assert abs(result.irr - 0.1524) < 0.005

    def test_npv_positive(self):
        """NPV at 10% for a good investment should be positive."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-100_000, 30_000, 30_000, 30_000, 30_000, 30_000],
            discount_rate=0.10,
        ))
        assert result.npv > 0
        # NPV at 10% ≈ $13,723.60
        assert abs(result.npv - 13_724) < 50

    def test_npv_at_irr_is_zero(self):
        """NPV computed at the IRR should be approximately zero."""
        cfs = [-100_000, 30_000, 30_000, 30_000, 30_000, 30_000]
        result = compute_irr_npv(CashFlowInput(cash_flows=cfs))
        # Now compute NPV at the IRR
        result2 = compute_irr_npv(CashFlowInput(
            cash_flows=cfs, discount_rate=result.irr
        ))
        assert abs(result2.npv) < 1.0  # Should be ~0

    def test_negative_npv(self):
        """Bad investment: NPV should be negative at high discount rate."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-100_000, 20_000, 20_000, 20_000, 20_000, 20_000],
            discount_rate=0.20,
        ))
        assert result.npv < 0

    def test_payback_period(self):
        """$100K investment with $25K/year → payback at year 4."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-100_000, 25_000, 25_000, 25_000, 25_000, 25_000]
        ))
        assert result.payback_period == 4.0

    def test_payback_fractional(self):
        """$100K with uneven flows → fractional payback."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-100_000, 40_000, 40_000, 40_000]
        ))
        # After 2 years: -20K. 3rd year brings +40K → payback at 2.5
        assert abs(result.payback_period - 2.5) < 0.01

    def test_real_estate_cash_flows(self):
        """Realistic RE deal: buy, hold 5 years, sell."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[
                -500_000,   # Purchase
                45_000,     # Year 1 NOI
                47_000,     # Year 2 NOI
                49_000,     # Year 3 NOI
                51_000,     # Year 4 NOI
                550_000,    # Year 5 NOI + sale proceeds
            ],
            discount_rate=0.08,
        ))
        assert result.irr is not None
        assert 0.05 < result.irr < 0.15
        assert result.npv is not None

    def test_validation_too_few_flows(self):
        with pytest.raises(ValueError, match="at least 2"):
            compute_irr_npv(CashFlowInput(cash_flows=[-100]))

    def test_validation_all_positive(self):
        with pytest.raises(ValueError, match="both positive and negative"):
            compute_irr_npv(CashFlowInput(cash_flows=[100, 200, 300]))

    def test_high_return_irr(self):
        """Very profitable deal — IRR should still converge."""
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-10_000, 50_000]
        ))
        # 50K/10K - 1 = 400% return
        assert result.irr is not None
        assert result.irr > 3.0


# ═══════════════════════════════════════════════════════════════
# DSCR TESTS
# ═══════════════════════════════════════════════════════════════

class TestDSCR:
    def test_strong_coverage(self):
        """NOI $150K, debt service $100K → DSCR 1.5 (strong)."""
        result = compute_dscr(DSCRInput(noi=150_000, annual_debt_service=100_000))
        assert result.ratio == 1.5
        assert result.assessment == "strong"

    def test_adequate_coverage(self):
        """DSCR 1.15 → adequate."""
        result = compute_dscr(DSCRInput(noi=115_000, annual_debt_service=100_000))
        assert result.ratio == 1.15
        assert result.assessment == "adequate"

    def test_weak_coverage(self):
        """DSCR 1.05 → weak."""
        result = compute_dscr(DSCRInput(noi=105_000, annual_debt_service=100_000))
        assert result.ratio == 1.05
        assert result.assessment == "weak"

    def test_critical_coverage(self):
        """DSCR 0.90 → critical (can't cover debt)."""
        result = compute_dscr(DSCRInput(noi=90_000, annual_debt_service=100_000))
        assert result.ratio == 0.9
        assert result.assessment == "critical"

    def test_exact_break_even(self):
        """DSCR 1.0 → weak (not adequate)."""
        result = compute_dscr(DSCRInput(noi=100_000, annual_debt_service=100_000))
        assert result.ratio == 1.0
        assert result.assessment == "weak"

    def test_validation(self):
        with pytest.raises(ValueError, match="debt service must be positive"):
            compute_dscr(DSCRInput(noi=100_000, annual_debt_service=0))


# ═══════════════════════════════════════════════════════════════
# CAP RATE TESTS
# ═══════════════════════════════════════════════════════════════

class TestCapRate:
    def test_solve_cap_rate(self):
        """NOI $75K, Value $1M → cap rate 7.5%."""
        result = solve_cap_rate(CapRateInput(
            noi=75_000, value=1_000_000, cap_rate=None
        ))
        assert result.cap_rate == 0.075
        assert result.solved_for == "cap_rate"

    def test_solve_noi(self):
        """Cap rate 6%, Value $2M → NOI $120K."""
        result = solve_cap_rate(CapRateInput(
            cap_rate=0.06, value=2_000_000, noi=None
        ))
        assert result.noi == 120_000.0
        assert result.solved_for == "noi"

    def test_solve_value(self):
        """Cap rate 8%, NOI $80K → Value $1M."""
        result = solve_cap_rate(CapRateInput(
            cap_rate=0.08, noi=80_000, value=None
        ))
        assert result.value == 1_000_000.0
        assert result.solved_for == "value"

    def test_low_cap_rate(self):
        """Premium property: NOI $50K, Value $1.25M → 4% cap."""
        result = solve_cap_rate(CapRateInput(
            noi=50_000, value=1_250_000, cap_rate=None
        ))
        assert result.cap_rate == 0.04

    def test_validation_wrong_nones(self):
        with pytest.raises(ValueError, match="Exactly one"):
            solve_cap_rate(CapRateInput(cap_rate=None, noi=None, value=100))

    def test_zero_value_error(self):
        with pytest.raises(ValueError, match="Value cannot be zero"):
            solve_cap_rate(CapRateInput(noi=100, value=0, cap_rate=None))

    def test_zero_cap_rate_error(self):
        with pytest.raises(ValueError, match="Cap rate cannot be zero"):
            solve_cap_rate(CapRateInput(cap_rate=0, noi=100, value=None))


# ═══════════════════════════════════════════════════════════════
# SENSITIVITY ANALYSIS TESTS
# ═══════════════════════════════════════════════════════════════

class TestSensitivity:
    def _dscr_fn(self, params):
        """Simple DSCR calculator for sensitivity testing."""
        return params["noi"] / params["debt_service"]

    def test_basic_matrix(self):
        """Vary NOI and debt service, check DSCR matrix."""
        result = sensitivity_matrix(SensitivityInput(
            base_case={"noi": 100_000, "debt_service": 80_000},
            row_variable="noi",
            row_values=[80_000, 90_000, 100_000, 110_000, 120_000],
            col_variable="debt_service",
            col_values=[70_000, 80_000, 90_000],
            compute_fn=self._dscr_fn,
            output_label="DSCR",
        ))

        assert result.output_label == "DSCR"
        assert len(result.matrix) == 5       # 5 rows
        assert len(result.matrix[0]) == 3    # 3 columns
        # Base case: 100K/80K = 1.25
        assert result.base_case_value == 1.25
        # Corner: NOI=120K, debt=70K = 1.7143
        assert abs(result.matrix[4][0] - 1.7143) < 0.001

    def test_irr_sensitivity(self):
        """Sensitivity of IRR to purchase price and annual NOI."""
        def irr_fn(params):
            cfs = [-params["purchase_price"]]
            for _ in range(5):
                cfs.append(params["annual_noi"])
            cfs[-1] += params["sale_price"]  # Add sale to last year
            from engine.financial.tools import _solve_irr
            irr = _solve_irr(cfs)
            return irr if irr else 0.0

        result = sensitivity_matrix(SensitivityInput(
            base_case={
                "purchase_price": 500_000,
                "annual_noi": 50_000,
                "sale_price": 550_000,
            },
            row_variable="purchase_price",
            row_values=[450_000, 500_000, 550_000],
            col_variable="annual_noi",
            col_values=[40_000, 50_000, 60_000],
            compute_fn=irr_fn,
            output_label="IRR",
        ))

        assert len(result.matrix) == 3
        assert len(result.matrix[0]) == 3
        # Higher price, lower NOI → lower IRR (top-right < bottom-left)
        assert result.matrix[2][0] < result.matrix[0][2]

    def test_validation_missing_variable(self):
        with pytest.raises(ValueError, match="not in base case"):
            sensitivity_matrix(SensitivityInput(
                base_case={"a": 1},
                row_variable="b",
                row_values=[1, 2],
                col_variable="a",
                col_values=[1, 2],
                compute_fn=lambda p: p["a"],
            ))

    def test_validation_no_fn(self):
        with pytest.raises(ValueError, match="compute_fn is required"):
            sensitivity_matrix(SensitivityInput(
                base_case={"a": 1, "b": 2},
                row_variable="a",
                row_values=[1, 2],
                col_variable="b",
                col_values=[1, 2],
                compute_fn=None,
            ))


# ═══════════════════════════════════════════════════════════════
# SERIALIZATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestSerialization:
    def test_amortization_to_dict(self):
        result = amortize(AmortizationInput(
            principal=10_000, annual_rate=0.06, term_months=12
        ))
        d = to_dict(result)
        assert isinstance(d, dict)
        assert "monthly_payment" in d
        assert "schedule" in d
        assert isinstance(d["schedule"], list)
        assert isinstance(d["schedule"][0], dict)

    def test_irr_to_dict(self):
        result = compute_irr_npv(CashFlowInput(
            cash_flows=[-100, 50, 50, 50],
            discount_rate=0.10,
        ))
        d = to_dict(result)
        assert "irr" in d
        assert "npv" in d
        assert isinstance(d["irr"], float)

    def test_summary_format(self):
        result = amortize(AmortizationInput(
            principal=100_000, annual_rate=0.06, term_months=360
        ))
        summary = format_amortization_summary(result)
        assert "schedule_preview" in summary
        assert "schedule_length" in summary
        assert summary["schedule_length"] == 360


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: REALISTIC DEAL ANALYSIS
# ═══════════════════════════════════════════════════════════════

class TestRealisticDealAnalysis:
    """End-to-end: use multiple tools together like a real deal pipeline."""

    def test_gas_station_egm_deal(self):
        """
        Scenario: Acquire gas station with 5 VGTs.
        Purchase: $800K, financed at 6.5% for 25 years.
        NOI: $85K/year from operations + ~$20K/year from gaming.
        """
        # Step 1: Amortization for the loan
        loan = amortize(AmortizationInput(
            principal=640_000,        # 80% LTV
            annual_rate=0.065,
            term_months=300,          # 25 years
        ))
        annual_debt_service = loan.monthly_payment * 12

        # Step 2: DSCR with combined income
        total_noi = 85_000 + 20_000  # Operations + gaming
        dscr = compute_dscr(DSCRInput(
            noi=total_noi,
            annual_debt_service=annual_debt_service,
        ))

        # Step 3: Cap rate
        cap = solve_cap_rate(CapRateInput(
            noi=total_noi, value=800_000, cap_rate=None
        ))

        # Step 4: IRR (5-year hold, sell at entry cap + 50bps)
        exit_cap = cap.cap_rate + 0.005
        exit_value = total_noi / exit_cap
        equity = 800_000 - 640_000  # $160K down

        annual_cf = total_noi - annual_debt_service
        irr_result = compute_irr_npv(CashFlowInput(
            cash_flows=[
                -equity,
                annual_cf,
                annual_cf,
                annual_cf,
                annual_cf,
                annual_cf + exit_value - 640_000,  # Simplified: sell + pay off loan
            ],
            discount_rate=0.10,
        ))

        # Assertions: all tools work together
        assert loan.monthly_payment > 0
        assert dscr.ratio > 1.0
        assert dscr.assessment in ("strong", "adequate")
        assert 0.10 < cap.cap_rate < 0.15
        assert irr_result.irr is not None
        assert irr_result.irr > 0  # Should be a positive return

    def test_sensitivity_on_deal(self):
        """Sensitivity: how does NOI and cap rate affect deal value?"""
        result = sensitivity_matrix(SensitivityInput(
            base_case={"noi": 105_000, "cap_rate": 0.08},
            row_variable="noi",
            row_values=[85_000, 95_000, 105_000, 115_000, 125_000],
            col_variable="cap_rate",
            col_values=[0.065, 0.075, 0.085, 0.095],
            compute_fn=lambda p: p["noi"] / p["cap_rate"],
            output_label="Implied Value ($)",
        ))

        # Base case: 105K / 0.08 = $1,312,500
        assert abs(result.base_case_value - 1_312_500) < 1
        # Higher NOI + lower cap → highest value (bottom-left)
        assert result.matrix[4][0] > result.matrix[0][3]
