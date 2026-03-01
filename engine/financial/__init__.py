"""
engine.financial — Micro Financial Tools Suite
================================================
Phase 2: Deterministic financial calculators.

Usage:
    from engine.financial import amortize, AmortizationInput
    from engine.financial import compute_irr_npv, CashFlowInput
    from engine.financial import compute_dscr, DSCRInput
    from engine.financial import solve_cap_rate, CapRateInput
    from engine.financial import tvm_solve, TVMInput
    from engine.financial import sensitivity_matrix, SensitivityInput
"""

from engine.financial.tools import (
    # Amortization
    amortize, AmortizationInput, AmortizationOutput, AmortizationPayment,
    # TVM
    tvm_solve, TVMInput, TVMOutput,
    # IRR / NPV
    compute_irr_npv, CashFlowInput, IRRNPVOutput,
    # DSCR
    compute_dscr, DSCRInput, DSCROutput,
    # Cap Rate
    solve_cap_rate, CapRateInput, CapRateOutput,
    # Sensitivity
    sensitivity_matrix, SensitivityInput, SensitivityOutput,
    # Registry
    FINANCIAL_TOOLS,
)

from engine.financial.serialization import to_dict

__all__ = [
    "amortize", "AmortizationInput", "AmortizationOutput", "AmortizationPayment",
    "tvm_solve", "TVMInput", "TVMOutput",
    "compute_irr_npv", "CashFlowInput", "IRRNPVOutput",
    "compute_dscr", "DSCRInput", "DSCROutput",
    "solve_cap_rate", "CapRateInput", "CapRateOutput",
    "sensitivity_matrix", "SensitivityInput", "SensitivityOutput",
    "FINANCIAL_TOOLS", "to_dict",
]
