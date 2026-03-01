"""
engine.contracts.analyzer — Deal Analyzer Service
=====================================================
Phase 5: Orchestrates the full contract analysis workflow:
  1. Load/validate contract template
  2. Pull predictions from Phase 4 (or accept manual inputs)
  3. Run Monte Carlo simulation
  4. Compute guardrails and recommendations
  5. Persist results for audit

Usage:
    analyzer = DealAnalyzer(session, workspace_id="ws1")
    result = analyzer.analyze_deal(
        template_id=1,
        overrides={"operator_split": 0.60, "host_split": 0.40},
        prediction={"coin_in": {"p10": 50000, "p50": 80000, "p90": 120000}, ...},
    )
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from engine.contracts.templates import (
    validate_terms, apply_overrides,
    compute_monthly_operator_cash, compute_monthly_debt_service,
)
from engine.contracts.montecarlo import (
    SimulationInputs, run_simulation, compare_structures,
)


class DealAnalyzer:
    """Unified deal analysis service."""

    def __init__(self, session=None, workspace_id: str = "", user_id: str = ""):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id

    def analyze_deal(
        self,
        agreement_type: str = "revenue_share",
        terms: Optional[Dict] = None,
        prediction: Optional[Dict] = None,
        template_id: Optional[int] = None,
        overrides: Optional[Dict] = None,
        num_simulations: int = 10000,
        scenario_name: str = "",
        seed: Optional[int] = None,
    ) -> Dict:
        """Run a complete deal analysis.

        Either provide terms directly, or load from template_id with overrides.
        Prediction should have coin_in/hold_pct quantiles from Phase 4.
        """
        start = time.perf_counter()

        # 1. Resolve terms
        if template_id and self._session:
            from engine.db.contract_repositories import ContractTemplateRepo
            repo = ContractTemplateRepo(self._session)
            tmpl = repo.get(template_id)
            if not tmpl:
                return {"error": f"Template {template_id} not found"}
            agreement_type = tmpl["agreement_type"]
            final_terms = tmpl["terms"]
            if overrides:
                final_terms = apply_overrides(final_terms, overrides)
            constraints = tmpl.get("constraints")
        else:
            final_terms = terms or {}
            constraints = None

        # 2. Validate
        errors = validate_terms(agreement_type, final_terms, constraints)
        if errors:
            return {
                "error": "Validation failed",
                "validation_errors": [
                    {"field": e.field, "message": e.message, "value": e.value}
                    for e in errors
                ],
            }

        # 3. Build simulation inputs
        pred = prediction or {}
        ci = pred.get("coin_in", {})
        hp = pred.get("hold_pct", {})

        sim_inputs = SimulationInputs(
            coin_in_p10=ci.get("p10", 50000),
            coin_in_p50=ci.get("p50", 80000),
            coin_in_p90=ci.get("p90", 120000),
            hold_pct_p10=hp.get("p10", 0.22),
            hold_pct_p50=hp.get("p50", 0.26),
            hold_pct_p90=hp.get("p90", 0.31),
            agreement_type=agreement_type,
            operator_split=final_terms.get("operator_split", 0.65),
            host_split=final_terms.get("host_split", 0.35),
            monthly_lease=final_terms.get("monthly_lease", 2000),
            base_lease=final_terms.get("base_lease", 1500),
            threshold=final_terms.get("threshold", 20000),
            contract_months=final_terms.get("contract_months", 60),
            terminal_count=final_terms.get("terminal_count", 5),
            acquisition_type=final_terms.get("acquisition_type", "cash"),
            acquisition_cost=final_terms.get("acquisition_cost", 150000),
            down_payment_pct=final_terms.get("down_payment_pct", 0.20),
            annual_rate=final_terms.get("annual_rate", 0.085),
            loan_term_months=final_terms.get("loan_term_months", 48),
            num_simulations=num_simulations,
            seed=seed,
        )

        # 4. Run simulation
        sim_result = run_simulation(sim_inputs)

        # 5. Build response
        result = {
            "scenario_name": scenario_name,
            "agreement_type": agreement_type,
            "terms": final_terms,
            "irr": {
                "p10": sim_result.irr_p10,
                "p25": sim_result.irr_p25,
                "p50": sim_result.irr_p50,
                "p75": sim_result.irr_p75,
                "p90": sim_result.irr_p90,
                "mean": sim_result.irr_mean,
            },
            "net_win": {
                "p10": sim_result.net_win_p10,
                "p50": sim_result.net_win_p50,
                "p90": sim_result.net_win_p90,
            },
            "operator_cash_flow": {
                "p10": sim_result.operator_cf_p10,
                "p50": sim_result.operator_cf_p50,
                "p90": sim_result.operator_cf_p90,
            },
            "risk": {
                "prob_negative_irr": sim_result.prob_negative_irr,
                "prob_below_10pct": sim_result.prob_below_10pct,
                "prob_below_20pct": sim_result.prob_below_20pct,
            },
            "guardrails": {
                "breakeven_net_win": sim_result.breakeven_net_win,
                "target_net_win_20pct_irr": sim_result.target_net_win_20pct,
            },
            "simulation": {
                "num_simulations": sim_result.num_simulations,
                "valid_simulations": sim_result.valid_simulations,
                "execution_ms": sim_result.execution_ms,
            },
        }

        # 6. Persist
        if self._session:
            try:
                from engine.db.contract_repositories import SimulationRunRepo
                repo = SimulationRunRepo(self._session)
                run = repo.create(
                    workspace_id=self._workspace_id,
                    inputs={
                        "agreement_type": agreement_type,
                        "terms": final_terms,
                        "prediction": pred,
                    },
                    results=result,
                    num_simulations=num_simulations,
                    template_id=template_id,
                    overrides=overrides,
                    scenario_name=scenario_name,
                    execution_ms=sim_result.execution_ms,
                    user_id=self._user_id,
                )
                result["run_id"] = run["id"]
            except Exception:
                pass

        elapsed = int((time.perf_counter() - start) * 1000)
        result["total_ms"] = elapsed

        return result

    def compare_deals(
        self,
        prediction: Dict,
        structures: List[Dict],
        acquisition_cost: float = 150000,
        num_simulations: int = 5000,
        seed: Optional[int] = None,
    ) -> Dict:
        """Compare multiple contract structures.

        Args:
            prediction: Phase 4 prediction with quantiles
            structures: List of dicts with agreement_type + terms
            acquisition_cost: Cost to acquire the location

        Returns ranked comparison.
        """
        ci = prediction.get("coin_in", {})
        hp = prediction.get("hold_pct", {})

        base = SimulationInputs(
            coin_in_p10=ci.get("p10", 50000),
            coin_in_p50=ci.get("p50", 80000),
            coin_in_p90=ci.get("p90", 120000),
            hold_pct_p10=hp.get("p10", 0.22),
            hold_pct_p50=hp.get("p50", 0.26),
            hold_pct_p90=hp.get("p90", 0.31),
            acquisition_cost=acquisition_cost,
            num_simulations=num_simulations,
            seed=seed,
        )

        ranked = compare_structures(base, structures)

        return {
            "ranked_structures": ranked,
            "best": ranked[0] if ranked else None,
            "num_structures": len(ranked),
        }

    def compute_terminal_recommendation(
        self,
        prediction_per_terminal: Dict,
        agreement_type: str = "revenue_share",
        terms: Optional[Dict] = None,
        acquisition_cost_per_terminal: float = 30000,
        target_irr: float = 0.20,
        max_terminals: int = 10,
        num_simulations: int = 2000,
    ) -> Dict:
        """Recommend optimal terminal count for target IRR.

        Runs simulation for 1..max_terminals and finds the sweet spot.
        """
        terms = terms or {
            "operator_split": 0.65, "host_split": 0.35,
            "contract_months": 60,
        }

        results = []
        ci_per = prediction_per_terminal.get("coin_in", {})
        hp = prediction_per_terminal.get("hold_pct", {})

        for tc in range(1, max_terminals + 1):
            sim_inputs = SimulationInputs(
                coin_in_p10=ci_per.get("p10", 15000) * tc,
                coin_in_p50=ci_per.get("p50", 20000) * tc,
                coin_in_p90=ci_per.get("p90", 28000) * tc,
                hold_pct_p10=hp.get("p10", 0.22),
                hold_pct_p50=hp.get("p50", 0.26),
                hold_pct_p90=hp.get("p90", 0.31),
                agreement_type=agreement_type,
                acquisition_cost=acquisition_cost_per_terminal * tc,
                contract_months=terms.get("contract_months", 60),
                terminal_count=tc,
                num_simulations=num_simulations,
                **{k: v for k, v in terms.items()
                   if k in ("operator_split", "host_split", "monthly_lease",
                            "base_lease", "threshold")},
            )
            sim = run_simulation(sim_inputs)
            results.append({
                "terminal_count": tc,
                "irr_p50": sim.irr_p50,
                "irr_p10": sim.irr_p10,
                "operator_cf_p50": sim.operator_cf_p50,
                "prob_negative": sim.prob_negative_irr,
                "total_cost": acquisition_cost_per_terminal * tc,
            })

        # Find optimal: highest IRR that meets target
        meeting_target = [r for r in results if r["irr_p50"] >= target_irr]
        if meeting_target:
            recommended = max(meeting_target, key=lambda r: r["operator_cf_p50"])
        else:
            recommended = max(results, key=lambda r: r["irr_p50"]) if results else None

        return {
            "recommended": recommended,
            "target_irr": target_irr,
            "all_options": results,
        }
