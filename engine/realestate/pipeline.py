"""
engine.realestate.pipeline — Deal Evaluation Pipeline
=========================================================
Phase 6: Orchestrates all 7 stages and produces scored recommendation.

Usage:
    pipeline = DealPipeline(session, workspace_id="ws1")
    result = pipeline.evaluate({
        "deal_name": "123 Main St Strip Center",
        "property_type": "retail_strip",
        "purchase_price": 1500000,
        "noi": 120000,
        "address": "123 Main St, Springfield, IL",
        "state": "IL",
    })
    # result["decision"] == "GO" / "HOLD" / "NO_GO"
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from engine.realestate.stages import (
    stage_intake, stage_feasibility, stage_market,
    stage_cost, stage_finance, stage_risk, stage_decision,
)
from engine.realestate.templates import get_template_for_type


class DealPipeline:
    """End-to-end deal evaluation pipeline.

    Runs 7 stages sequentially:
      intake → feasibility → market → cost → finance → risk → decision

    Persists all stage results and final decision to deal_runs table.
    """

    def __init__(self, session=None, workspace_id: str = "", user_id: str = ""):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id

    def evaluate(
        self, inputs: Dict[str, Any],
        market_context: Dict = None,
        template_id: int = None,
    ) -> Dict[str, Any]:
        """Run the full deal evaluation pipeline.

        Args:
            inputs: Property data (purchase_price, noi, address, etc.)
            market_context: Optional market data (gaming_location_count, avg_nti, etc.)
            template_id: Optional DB template ID to use

        Returns:
            Complete deal evaluation with all stage results and decision.
        """
        start = time.perf_counter()

        deal_name = inputs.get("deal_name", inputs.get("address", "Unnamed Deal"))
        property_type = inputs.get("property_type", "retail_strip")

        # Load template
        template = self._load_template(property_type, template_id)
        defaults = template.get("defaults", {})
        weights = template.get("scoring_weights", {})

        # Create deal run in DB
        deal_id = None
        if self._session:
            deal_id = self._create_deal_run(deal_name, property_type, inputs, template_id)

        stage_results = {}
        scores = {}

        try:
            # Stage 1: Intake
            intake = stage_intake(inputs, defaults)
            stage_results["intake"] = intake
            if intake["status"] == "fail":
                return self._finalize(
                    deal_id, stage_results, scores, "NO_GO",
                    f"Intake validation failed: {intake['errors']}",
                    start,
                )

            params = intake["params"]

            # Stage 2: Feasibility
            feasibility = stage_feasibility(params)
            stage_results["feasibility"] = feasibility
            scores["feasibility"] = feasibility["score"]
            if feasibility["status"] == "fail":
                return self._finalize(
                    deal_id, stage_results, scores, "NO_GO",
                    f"Feasibility screen failed: {feasibility['flags']}",
                    start,
                )

            # Stage 3: Market
            market = stage_market(params, market_context)
            stage_results["market"] = market
            scores["market_strength"] = market["score"]

            # Stage 4: Cost
            cost = stage_cost(params)
            stage_results["cost"] = cost
            scores["cost_risk"] = cost["score"]

            # Stage 5: Finance
            finance = stage_finance(params)
            stage_results["finance"] = finance
            scores["financial_return"] = finance["score"]
            scores["debt_coverage"] = min(1.0, finance["dscr"] / 1.5)

            # Stage 6: Risk
            risk = stage_risk(params, finance)
            stage_results["risk"] = risk

            # Gaming upside score
            if params.get("gaming_eligible"):
                gaming_data = risk.get("scenarios", {}).get("gaming_upside", {})
                gaming_dscr = gaming_data.get("dscr_with_gaming", 0)
                scores["gaming_upside"] = min(1.0, gaming_dscr / 2.0) if gaming_dscr > 0 else 0.3
            else:
                scores["gaming_upside"] = 0.0

            # Stage 7: Decision
            decision_result = stage_decision(scores, weights, finance, risk)
            stage_results["decision"] = decision_result

            decision = decision_result["decision"]
            rationale = decision_result["rationale"]

        except Exception as e:
            if deal_id and self._session:
                self._fail_deal(deal_id, str(e))
            return {
                "deal_name": deal_name,
                "status": "failed",
                "error": str(e),
                "stage_results": stage_results,
            }

        return self._finalize(deal_id, stage_results, scores, decision, rationale, start)

    def evaluate_with_gaming(
        self, inputs: Dict, gaming_prediction: Dict = None,
        market_context: Dict = None,
    ) -> Dict:
        """Evaluate deal with integrated EGM prediction from Phase 4.

        gaming_prediction should be the output of PredictionService.predict():
          {"coin_in": {"p50": ...}, "hold_pct": {"p50": ...}, "net_win": {"p50": ...}}
        """
        if gaming_prediction:
            nw_p50 = gaming_prediction.get("net_win", {}).get("p50", 0)
            inputs["expected_gaming_net_win_monthly"] = nw_p50
            inputs["gaming_eligible"] = True

        return self.evaluate(inputs, market_context)

    # ── Internal helpers ──────────────────────────────────

    def _load_template(self, property_type: str, template_id: int = None) -> Dict:
        """Load template from DB or use defaults."""
        if template_id and self._session:
            from engine.db.deal_repositories import PropertyTemplateRepo
            repo = PropertyTemplateRepo(self._session)
            tmpl = repo.get(template_id)
            if tmpl:
                return tmpl

        return get_template_for_type(property_type)

    def _create_deal_run(self, deal_name, property_type, inputs, template_id) -> int:
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(self._session)
        run = repo.create(
            workspace_id=self._workspace_id,
            deal_name=deal_name,
            property_type=property_type,
            inputs=inputs,
            template_id=template_id,
            user_id=self._user_id,
        )
        return run["id"]

    def _finalize(
        self, deal_id, stage_results, scores, decision, rationale, start,
    ) -> Dict:
        elapsed = int((time.perf_counter() - start) * 1000)

        if deal_id and self._session:
            from engine.db.deal_repositories import DealRunRepo
            repo = DealRunRepo(self._session)
            repo.complete(deal_id, stage_results, scores, decision, rationale)

        result = {
            "deal_id": deal_id,
            "status": "completed",
            "decision": decision,
            "rationale": rationale,
            "scores": scores,
            "stage_results": stage_results,
            "execution_ms": elapsed,
        }

        # Surface key metrics at top level
        finance = stage_results.get("finance", {})
        risk = stage_results.get("risk", {})
        dec = stage_results.get("decision", {})

        result["summary"] = {
            "irr": finance.get("irr_estimate", 0),
            "dscr": finance.get("dscr", 0),
            "cash_on_cash": finance.get("cash_on_cash", 0),
            "cap_rate": finance.get("cap_rate", 0),
            "total_basis": finance.get("total_basis", 0),
            "equity_required": finance.get("equity_required", 0),
            "worst_case_dscr": risk.get("worst_case_dscr", 0),
            "composite_score": dec.get("composite_score", 0),
        }

        return result

    def _fail_deal(self, deal_id: int, error: str):
        from engine.db.deal_repositories import DealRunRepo
        repo = DealRunRepo(self._session)
        repo.fail(deal_id, error)
