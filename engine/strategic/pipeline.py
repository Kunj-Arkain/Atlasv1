"""
engine.strategic.pipeline — Production Strategic Pipeline
=============================================================
Orchestrates 5 cognitive stages with:
  - Real LLM reasoning (Anthropic/OpenAI via llm_client)
  - Live tool execution (EGM predict, Monte Carlo, portfolio, deals, financials)
  - OODA retry with validation issues fed back to the LLM
  - Per-stage model routing (Phase 7)
  - Full cost tracking and audit trail
  - Persistence to strategic_runs table

When ANTHROPIC_API_KEY is set, every stage uses real LLM + tool calls.
Without it, stages fall back to rule-based heuristics.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from engine.strategic.stages import (
    stage_compression,
    stage_decision_prep,
    stage_scenarios,
    stage_patterns,
    stage_synthesis,
    stage_data_gathering,
    stage_counterparty_risk,
    stage_legal_risk,
    stage_capital_stack,
)
from engine.strategic.schema import (
    ScenarioInput, StrategicAnalysisResult, SWOTAnalysis,
    ScenarioCase, FailureMode, NextAction, Decision,
)
from engine.brain.tools import ToolRegistry

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# STAGE-TO-ROUTE MAPPING (Phase 7)
# ═══════════════════════════════════════════════════════════════

DEFAULT_STAGE_ROUTES = {
    "compression":   "cheap_structured",
    "decision_prep": "cheap_structured",
    "scenarios":     "strategic_deep",
    "patterns":      "strategic_deep",
    "synthesis":     "strategic_deep",
}


# ═══════════════════════════════════════════════════════════════
# OODA VALIDATORS
# ═══════════════════════════════════════════════════════════════

def _validate_compression(result: Dict) -> List[str]:
    issues = []
    if result.get("status") != "pass":
        issues.append("Stage failed")
    if not result.get("structured_scenario"):
        issues.append("Structured scenario is empty")
    if not result.get("key_variables"):
        issues.append("No key variables identified")
    text = result.get("structured_scenario", "")
    if len(text) < 50 and not result.get("missing_info"):
        issues.append("Brief scenario but no missing_info flagged")
    return issues


def _validate_decision_prep(result: Dict) -> List[str]:
    issues = []
    if not result.get("decision_criteria"):
        issues.append("No decision criteria generated")
    if result.get("preliminary_decision") not in ("GO", "MODIFY", "NO_GO"):
        issues.append("preliminary_decision must be GO/MODIFY/NO_GO")
    return issues


def _validate_scenarios(result: Dict) -> List[str]:
    issues = []
    if len(result.get("cases", [])) < 3:
        issues.append("Need at least 3 scenario cases (base/bull/bear)")
    if not result.get("sensitivities"):
        issues.append("Sensitivities list is empty")
    return issues


def _validate_patterns(result: Dict) -> List[str]:
    issues = []
    if not result.get("failure_modes"):
        issues.append("No failure modes identified")
    if not result.get("leverage_points"):
        issues.append("No leverage points identified")
    return issues


def _validate_synthesis(result: Dict) -> List[str]:
    issues = []
    if result.get("decision") not in ("GO", "MODIFY", "NO_GO"):
        issues.append("decision must be GO/MODIFY/NO_GO")
    conf = result.get("confidence", -1)
    if not (0 <= conf <= 1):
        issues.append(f"confidence {conf} out of range [0,1]")
    swot = result.get("swot", {})
    if not any(swot.get(k) for k in ("strengths", "weaknesses", "opportunities", "threats")):
        issues.append("SWOT is completely empty")
    if not result.get("next_actions"):
        issues.append("No next actions generated")
    return issues


STAGE_VALIDATORS = {
    "compression": _validate_compression,
    "decision_prep": _validate_decision_prep,
    "scenarios": _validate_scenarios,
    "patterns": _validate_patterns,
    "synthesis": _validate_synthesis,
}


# ═══════════════════════════════════════════════════════════════
# TOOL HANDLER FACTORY
# ═══════════════════════════════════════════════════════════════

def build_tool_handlers(session=None, workspace_id: str = "", user_id: str = "") -> Dict[str, Any]:
    """Build a dict of tool_name → callable from real engine subsystems.

    Each handler is a function(params_dict) → result_dict.
    These are the REAL engine tools that the LLM can call during analysis.
    """
    handlers = {}

    # ── EGM Prediction ─────────────────────────────────────
    def handle_egm_predict(params: Dict) -> Dict:
        from engine.egm.prediction import PredictionService
        svc = PredictionService(session, workspace_id, user_id)
        return svc.predict(
            venue_type=params.get("venue_type", "bar"),
            state=params.get("state", "IL"),
            terminal_count=params.get("terminal_count", 5),
            municipality=params.get("municipality", ""),
            include_similar=True,
        )
    handlers["egm_predict"] = handle_egm_predict

    # ── EGM Market Health ──────────────────────────────────
    def handle_egm_market_health(params: Dict) -> Dict:
        from engine.egm.analytics import EGMAnalytics
        analytics = EGMAnalytics(session)
        return analytics.market_health(params.get("state", ""))
    handlers["egm_market_health"] = handle_egm_market_health

    # ── Monte Carlo Contract Simulation ────────────────────
    def handle_simulate_contract(params: Dict) -> Dict:
        from engine.contracts.montecarlo import SimulationInputs, run_simulation
        # Map params to SimulationInputs fields
        sim_params = {}
        field_map = {
            "agreement_type": "agreement_type",
            "operator_split": "operator_split",
            "host_split": "host_split",
            "acquisition_cost": "acquisition_cost",
            "contract_months": "contract_months",
            "terminal_count": "terminal_count",
            "coin_in_p10": "coin_in_p10",
            "coin_in_p50": "coin_in_p50",
            "coin_in_p90": "coin_in_p90",
            "hold_pct_p10": "hold_pct_p10",
            "hold_pct_p50": "hold_pct_p50",
            "hold_pct_p90": "hold_pct_p90",
            "num_simulations": "num_simulations",
            "seed": "seed",
            "monthly_lease": "monthly_lease",
            "down_payment_pct": "down_payment_pct",
            "annual_rate": "annual_rate",
            "loan_term_months": "loan_term_months",
        }
        for k, v in field_map.items():
            if k in params:
                sim_params[v] = params[k]
        inputs = SimulationInputs(**sim_params)
        result = run_simulation(inputs)
        return asdict(result)
    handlers["simulate_contract"] = handle_simulate_contract

    # ── Deal Evaluation ────────────────────────────────────
    def handle_evaluate_deal(params: Dict) -> Dict:
        from engine.realestate.pipeline import DealPipeline
        pipeline = DealPipeline(session, workspace_id, user_id)
        return pipeline.evaluate(params)
    handlers["evaluate_deal"] = handle_evaluate_deal

    # ── Portfolio Dashboard ────────────────────────────────
    def handle_portfolio_dashboard(params: Dict) -> Dict:
        from engine.portfolio.analytics import PortfolioAnalytics
        analytics = PortfolioAnalytics(session, workspace_id)
        return analytics.dashboard()
    handlers["portfolio_dashboard"] = handle_portfolio_dashboard

    # ── Deal Impact on Portfolio ───────────────────────────
    def handle_deal_impact(params: Dict) -> Dict:
        from engine.portfolio.analytics import PortfolioAnalytics
        analytics = PortfolioAnalytics(session, workspace_id)
        return analytics.new_deal_impact(params)
    handlers["deal_impact"] = handle_deal_impact

    # ── Amortization ───────────────────────────────────────
    def handle_amortize(params: Dict) -> Dict:
        from engine.financial.tools import AmortizationInput, amortize
        inp = AmortizationInput(
            principal=params.get("principal", 0),
            annual_rate=params.get("annual_rate", 0.07),
            term_months=params.get("months", params.get("term_months", 60)),
        )
        inp.validate()
        result = amortize(inp)
        return {
            "monthly_payment": result.monthly_payment,
            "total_interest": result.total_interest,
            "total_paid": result.total_paid,
            "annual_debt_service": result.monthly_payment * 12,
            "term_months": inp.term_months,
        }
    handlers["amortize"] = handle_amortize

    # ── DSCR ───────────────────────────────────────────────
    def handle_dscr(params: Dict) -> Dict:
        from engine.financial.tools import DSCRInput, compute_dscr
        inp = DSCRInput(
            noi=params.get("noi", 0),
            annual_debt_service=params.get("annual_debt_service", 1),
        )
        inp.validate()
        result = compute_dscr(inp)
        return {
            "dscr": result.ratio,
            "noi": result.noi,
            "annual_debt_service": result.annual_debt_service,
            "pass_125": result.ratio >= 1.25,
            "assessment": result.assessment,
        }
    handlers["dscr"] = handle_dscr

    # ── Web Search ─────────────────────────────────────────
    def handle_web_search(params: Dict) -> Dict:
        """Multi-provider web search (Serper + Anthropic + Google).

        Uses all available providers for breadth, deduplicates results.
        """
        query = params.get("query", "")
        if not query:
            return {"error": "No query provided", "results": ""}
        try:
            from engine.strategic.search_providers import multi_search
            resp = multi_search(query, num_results=8)
            return {
                "query": query,
                "results": resp.top_snippets or "No results found",
                "providers": resp.providers_used,
                "total_results": resp.total_results,
                "elapsed_ms": resp.elapsed_ms,
            }
        except Exception as e:
            # Fallback: direct Anthropic web_search
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001", max_tokens=1024,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=[{"role": "user", "content": f"Search for: {query}. Key facts only."}],
                )
                parts = [b.text for b in resp.content if hasattr(b, "text")]
                return {"query": query, "results": "\n".join(parts) or "No results"}
            except Exception as e2:
                return {"query": query, "error": str(e2), "results": "Search unavailable"}
    handlers["web_search"] = handle_web_search

    # ── News Search ────────────────────────────────────────
    def handle_news_search(params: Dict) -> Dict:
        """Search recent news via Serper news API."""
        query = params.get("query", "")
        if not query:
            return {"error": "No query", "results": ""}
        try:
            from engine.strategic.search_providers import multi_search
            resp = multi_search(query, num_results=5, search_type="news")
            return {"query": query, "results": resp.top_snippets, "providers": resp.providers_used}
        except Exception as e:
            return {"query": query, "error": str(e), "results": ""}
    handlers["news_search"] = handle_news_search

    # ── Local Places Search ────────────────────────────────
    def handle_local_search(params: Dict) -> Dict:
        """Search for local businesses/competition via Serper places."""
        query = params.get("query", "")
        location = params.get("location", "")
        if not query:
            return {"error": "No query", "results": ""}
        try:
            from engine.strategic.search_providers import multi_search
            resp = multi_search(query, num_results=5, search_type="places", location=location)
            return {"query": query, "results": resp.top_snippets, "providers": resp.providers_used}
        except Exception as e:
            return {"query": query, "error": str(e), "results": ""}
    handlers["local_search"] = handle_local_search

    # ── Market Research (full report) ──────────────────────
    def handle_market_research(params: Dict) -> Dict:
        from engine.strategic.research import MarketResearcher
        researcher = MarketResearcher(session, workspace_id, user_id)
        return researcher.research_site(**params)
    handlers["market_research"] = handle_market_research

    # ── Construction Estimate ──────────────────────────────
    def handle_construction_estimate(params: Dict) -> Dict:
        """Quick construction cost estimate from scope params."""
        from engine.construction.pipeline import ConstructionPipeline
        pipeline = ConstructionPipeline(session, workspace_id, user_id)
        return pipeline.quick_estimate(
            property_type=params.get("property_type", "gas_station"),
            project_type=params.get("project_type", "renovation"),
            sqft=params.get("sqft", params.get("total_sqft", 2000)),
            terminal_count=params.get("terminal_count", 0),
            state=params.get("state", "IL"),
            city=params.get("city", ""),
        )
    handlers["construction_estimate"] = handle_construction_estimate

    # ── Construction Feasibility ───────────────────────────
    def handle_construction_feasibility(params: Dict) -> Dict:
        """Full construction analysis from scope or documents."""
        from engine.construction.pipeline import ConstructionPipeline
        pipeline = ConstructionPipeline(session, workspace_id, user_id)
        return pipeline.analyze(
            scope=params.get("scope"),
            document_text=params.get("document_text", ""),
            address=params.get("address", ""),
            state=params.get("state", "IL"),
            city=params.get("city", ""),
            budget=params.get("budget", 0),
            acquisition_price=params.get("acquisition_price", 0),
            noi=params.get("noi", 0),
        )
    handlers["construction_feasibility"] = handle_construction_feasibility

    # ── Multi-Provider Search (Serper + Anthropic + Google) ─
    def handle_multi_search(params: Dict) -> Dict:
        from engine.strategic.search_providers import multi_search
        resp = multi_search(
            query=params.get("query", ""),
            num_results=params.get("num_results", 8),
            search_type=params.get("search_type", "search"),
            location=params.get("location", ""),
        )
        return {"query": resp.query, "snippets": resp.top_snippets,
                "result_count": resp.total_results, "providers": resp.providers_used}
    handlers["multi_search"] = handle_multi_search

    # ── News Search ───────────────────────────────────────
    def handle_news_search(params: Dict) -> Dict:
        from engine.strategic.search_providers import news_search
        return {"query": params.get("query", ""), "results": news_search(params.get("query", ""))}
    handlers["news_search"] = handle_news_search

    # ── Local Places Search ───────────────────────────────
    def handle_local_search(params: Dict) -> Dict:
        from engine.strategic.search_providers import local_search
        return {"query": params.get("query", ""),
                "results": local_search(params.get("query", ""), params.get("location", ""))}
    handlers["local_search"] = handle_local_search

    # ── Vector Store: Similar Sites ───────────────────────
    def handle_find_similar_sites(params: Dict) -> Dict:
        from engine.strategic.vector_store import VectorStore
        vs = VectorStore(workspace_id)
        results = vs.find_similar_sites(params.get("address", ""), top_k=params.get("top_k", 5))
        return {"similar_sites": results, "count": len(results)}
    handlers["find_similar_sites"] = handle_find_similar_sites

    # ── Vector Store: Construction Comps ──────────────────
    def handle_find_construction_comps(params: Dict) -> Dict:
        from engine.strategic.vector_store import VectorStore
        vs = VectorStore(workspace_id)
        results = vs.find_similar_construction(
            params.get("project_type", ""), params.get("location", ""), top_k=5)
        return {"comps": results, "count": len(results)}
    handlers["find_construction_comps"] = handle_find_construction_comps

    # ── Domain Tools (Phase 12) ───────────────────────────

    def handle_pull_comps(params: Dict) -> Dict:
        registry = ToolRegistry(session, workspace_id, user_id)
        registry._register_domain_tools()
        return registry._handle_pull_comps(params)
    handlers["pull_comps"] = handle_pull_comps

    def handle_county_tax(params: Dict) -> Dict:
        registry = ToolRegistry(session, workspace_id, user_id)
        registry._register_domain_tools()
        return registry._handle_county_tax(params)
    handlers["county_tax_lookup"] = handle_county_tax

    def handle_analyze_lease(params: Dict) -> Dict:
        registry = ToolRegistry(session, workspace_id, user_id)
        registry._register_domain_tools()
        return registry._handle_analyze_lease(params)
    handlers["analyze_lease"] = handle_analyze_lease

    def handle_term_sheets(params: Dict) -> Dict:
        registry = ToolRegistry(session, workspace_id, user_id)
        registry._register_domain_tools()
        return registry._handle_term_sheets(params)
    handlers["generate_term_sheets"] = handle_term_sheets

    def handle_eb5_jobs(params: Dict) -> Dict:
        registry = ToolRegistry(session, workspace_id, user_id)
        registry._register_domain_tools()
        return registry._handle_eb5_jobs(params)
    handlers["eb5_job_impact"] = handle_eb5_jobs

    return handlers


# ═══════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════

class StrategicPipeline:
    """Production 5-stage strategic analysis with LLM + tools."""

    MAX_RETRIES = 2

    def __init__(
        self,
        session=None,
        workspace_id: str = "",
        user_id: str = "",
        stage_routes: Optional[Dict[str, str]] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._stage_routes = stage_routes or dict(DEFAULT_STAGE_ROUTES)
        self._llm_client = None
        self._tool_handlers = None
        self._init_llm()

    @property
    def is_llm_active(self) -> bool:
        return self._llm_client is not None

    def _init_llm(self):
        """Lazy-init LLM client (only when keys present)."""
        if self._llm_client is not None:
            return

        if os.environ.get("ANTHROPIC_API_KEY"):
            from engine.strategic.llm_client import LLMClient
            self._llm_client = LLMClient()
            self._tool_handlers = build_tool_handlers(
                self._session, self._workspace_id, self._user_id,
            )
            logger.info("Strategic pipeline: LLM client initialized (live mode)")
        else:
            logger.info("Strategic pipeline: No API key, using rule-based fallback")

    def analyze(
        self,
        inputs: Dict[str, Any],
        scenario_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the full 5-stage strategic analysis pipeline.

        With ANTHROPIC_API_KEY set:
          → LLM reasons through each stage
          → Calls real tools (EGM predict, Monte Carlo, portfolio, financials)
          → OODA retries feed validation issues back to the LLM

        Without:
          → Falls back to rule-based heuristics
        """
        self._init_llm()

        start = time.perf_counter()
        run_id = f"srun_{uuid.uuid4().hex[:12]}"
        scenario_id = scenario_id or f"scen_{uuid.uuid4().hex[:12]}"
        title = inputs.get("title", "Untitled")

        stage_results = {}
        stage_routes_used = {}
        total_llm_cost = 0.0

        llm = self._llm_client
        tools = self._tool_handlers

        # ── Stage 1: Compression ──────────────────────────
        route = self._stage_routes.get("compression", "cheap_structured")
        compression = self._run_stage_with_retry(
            "compression",
            lambda: stage_compression(
                inputs, llm_client=llm, tool_handlers=tools, route_tier=route,
            ),
        )
        stage_results["compression"] = compression
        stage_routes_used["compression"] = route
        total_llm_cost += self._extract_stage_cost(compression)

        if compression.get("status") == "fail":
            return self._build_result(
                scenario_id, run_id, title, "NO_GO", 0.0,
                f"Compression failed: {compression.get('errors', [])}",
                stage_results, stage_routes_used, start, total_llm_cost,
            )

        # ── Stage 2: Decision Prep ────────────────────────
        route = self._stage_routes.get("decision_prep", "cheap_structured")
        decision_prep = self._run_stage_with_retry(
            "decision_prep",
            lambda: stage_decision_prep(
                inputs, compression,
                llm_client=llm, tool_handlers=tools, route_tier=route,
            ),
        )
        stage_results["decision_prep"] = decision_prep
        stage_routes_used["decision_prep"] = route
        total_llm_cost += self._extract_stage_cost(decision_prep)

        # ── Stage 3: Scenarios ────────────────────────────
        route = self._stage_routes.get("scenarios", "strategic_deep")
        scenarios = self._run_stage_with_retry(
            "scenarios",
            lambda: stage_scenarios(
                inputs, compression, decision_prep,
                llm_client=llm, tool_handlers=tools, route_tier=route,
            ),
        )
        stage_results["scenarios"] = scenarios
        stage_routes_used["scenarios"] = route
        total_llm_cost += self._extract_stage_cost(scenarios)

        # ── Stage 4: Patterns ─────────────────────────────
        route = self._stage_routes.get("patterns", "strategic_deep")
        patterns = self._run_stage_with_retry(
            "patterns",
            lambda: stage_patterns(
                inputs, compression, decision_prep, scenarios,
                llm_client=llm, tool_handlers=tools, route_tier=route,
            ),
        )
        stage_results["patterns"] = patterns
        stage_routes_used["patterns"] = route
        total_llm_cost += self._extract_stage_cost(patterns)

        # ── Stage 5: Synthesis ────────────────────────────
        route = self._stage_routes.get("synthesis", "strategic_deep")
        synthesis = self._run_stage_with_retry(
            "synthesis",
            lambda: stage_synthesis(
                inputs, compression, decision_prep, scenarios, patterns,
                llm_client=llm, route_tier=route,
            ),
        )
        stage_results["synthesis"] = synthesis
        stage_routes_used["synthesis"] = route
        total_llm_cost += self._extract_stage_cost(synthesis)

        return self._build_result(
            scenario_id, run_id, title,
            synthesis.get("decision", "MODIFY"),
            synthesis.get("confidence", 0.5),
            synthesis.get("decision_rationale", ""),
            stage_results, stage_routes_used, start, total_llm_cost,
            synthesis=synthesis, scenarios=scenarios,
            patterns=patterns, compression=compression,
        )

    def _extract_stage_cost(self, stage_result: Dict) -> float:
        """Pull LLM cost from a stage result if present."""
        llm_info = stage_result.get("_llm_response", {})
        return llm_info.get("cost", 0.0)

    def _run_stage_with_retry(self, stage_name: str, run_fn) -> Dict:
        """Execute with OODA retry. On LLM path, validation issues are
        appended to context so the LLM can self-correct."""
        validator = STAGE_VALIDATORS.get(stage_name)

        for attempt in range(1, self.MAX_RETRIES + 2):
            result = run_fn()

            if not validator:
                return result

            issues = validator(result)
            if not issues:
                if attempt > 1:
                    result["_retry_count"] = attempt - 1
                return result

            if attempt <= self.MAX_RETRIES:
                result["_validation_issues"] = issues
                result["_retry_attempt"] = attempt
                logger.info(f"OODA retry {attempt} for {stage_name}: {issues}")
                continue

            result["_validation_issues"] = issues
            result["_retries_exhausted"] = True
            return result

        return result

    def _build_result(
        self,
        scenario_id, run_id, title,
        decision, confidence, rationale,
        stage_results, stage_routes, start, llm_cost,
        synthesis=None, scenarios=None, patterns=None, compression=None,
    ) -> Dict:
        elapsed = int((time.perf_counter() - start) * 1000)

        result = StrategicAnalysisResult(
            scenario_id=scenario_id,
            run_id=run_id,
            title=title,
            decision=decision,
            confidence=confidence,
            decision_rationale=rationale,
            stage_results=stage_results,
            stage_routes=stage_routes,
            elapsed_ms=elapsed,
            llm_cost_usd=llm_cost,
        )

        if synthesis:
            swot_data = synthesis.get("swot", {})
            result.swot = SWOTAnalysis(
                strengths=swot_data.get("strengths", []),
                weaknesses=swot_data.get("weaknesses", []),
                opportunities=swot_data.get("opportunities", []),
                threats=swot_data.get("threats", []),
            )
            result.next_actions = [
                NextAction(**a) if isinstance(a, dict) else a
                for a in synthesis.get("next_actions", [])
            ]

        if scenarios:
            result.scenarios = [
                ScenarioCase(**c) if isinstance(c, dict) else c
                for c in scenarios.get("cases", [])
            ]
            result.sensitivities = scenarios.get("sensitivities", [])
            result.second_order_effects = scenarios.get("second_order_effects", [])

        if patterns:
            result.failure_modes = [
                FailureMode(**fm) if isinstance(fm, dict) else fm
                for fm in patterns.get("failure_modes", [])
            ]
            result.leverage_points = patterns.get("leverage_points", [])
            result.contradictions = patterns.get("contradictions", [])

        if compression:
            result.missing_info = compression.get("missing_info", [])

        if self._session:
            self._persist_run(result, llm_cost)

        out = result.to_dict()
        out["llm_active"] = self.is_llm_active
        out["llm_costs_by_stage"] = {
            s: self._extract_stage_cost(stage_results.get(s, {}))
            for s in ("compression", "decision_prep", "scenarios", "patterns", "synthesis")
            if s in stage_results
        }
        out["status"] = "completed"
        return out

    def _persist_run(self, result: StrategicAnalysisResult, llm_cost: float):
        try:
            from engine.db.strategic_repositories import StrategicRunRepo
            repo = StrategicRunRepo(self._session)
            repo.create_run(
                workspace_id=self._workspace_id,
                scenario_id=result.scenario_id,
                run_id=result.run_id,
                title=result.title,
                decision=result.decision,
                confidence=result.confidence,
                outputs=result.to_dict(),
                elapsed_ms=result.elapsed_ms,
                llm_cost_usd=llm_cost,
                stage_routes=result.stage_routes,
            )
        except Exception as e:
            logger.warning(f"Failed to persist strategic run: {e}")

    # ── Convenience methods ───────────────────────────────

    def swot_only(self, inputs: Dict) -> Dict:
        result = self.analyze(inputs)
        return result.get("swot", {})

    def stress_test(self, inputs: Dict) -> Dict:
        result = self.analyze(inputs)
        return {
            "decision": result.get("decision"),
            "failure_modes": result.get("failure_modes", []),
            "contradictions": result.get("contradictions", []),
            "second_order_effects": result.get("second_order_effects", []),
            "confidence": result.get("confidence"),
        }

    def scenario_simulate(self, inputs: Dict) -> Dict:
        result = self.analyze(inputs)
        return {
            "scenarios": result.get("scenarios", []),
            "sensitivities": result.get("sensitivities", []),
            "second_order_effects": result.get("second_order_effects", []),
        }

    def assumption_audit(self, inputs: Dict) -> Dict:
        self._init_llm()
        route = self._stage_routes.get("compression", "cheap_structured")
        compression = stage_compression(
            inputs,
            llm_client=self._llm_client,
            tool_handlers=self._tool_handlers,
            route_tier=route,
        )
        return {
            "explicit_assumptions": compression.get("explicit_assumptions", []),
            "missing_info": compression.get("missing_info", []),
            "key_variables": compression.get("key_variables", []),
            "scope_boundaries": compression.get("scope_boundaries", []),
        }
