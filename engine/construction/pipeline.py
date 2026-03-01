"""
engine.construction.pipeline — Construction Analysis Pipeline
================================================================
Orchestrates document ingestion → scope extraction → cost estimation →
schedule → manpower → feasibility assessment.

Feeds into strategic pipeline as a first-class input to go/no-go decisions.

With LLM (ANTHROPIC_API_KEY):
  - Extracts scope from uploaded architectural/MEP documents
  - Searches web for current local material prices and labor rates
  - Uses historical data from vector store for comp-based estimation
  - LLM-synthesized feasibility assessment

Without LLM:
  - Uses user-provided scope dict
  - RSMeans-benchmarked cost estimation
  - Template-based schedule generation
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SCOPE EXTRACTION PROMPT
# ═══════════════════════════════════════════════════════════════

SCOPE_EXTRACTION_PROMPT = """You are a senior construction estimator extracting scope from project documents.

Analyze the provided document text and extract a comprehensive construction scope. Be specific about quantities, materials, and systems.

You have tools available:
- web_search: Look up current material prices, labor rates, code requirements
- Use it to find local construction costs and regulatory requirements

Respond with ONLY a JSON object:
{
  "project_name": "<name>",
  "project_type": "renovation|new_build|tenant_improvement|addition",
  "property_type": "gas_station|restaurant|bar|retail|office|warehouse|mixed_use",
  "total_sqft": <number>,
  "stories": <number>,
  "construction_type": "<IBC type if determinable>",

  "demolition_sqft": <number or 0>,
  "new_construction_sqft": <number or 0>,
  "renovation_sqft": <number or 0>,
  "exterior_work": ["<item>", ...],
  "interior_finishes": ["<item>", ...],
  "ada_compliance": true/false,

  "hvac_scope": "new_system|replace|repair|none",
  "hvac_tons": <number>,
  "electrical_service": "200A|400A|600A",
  "electrical_upgrades": ["<item>", ...],
  "plumbing_fixtures": <count>,
  "plumbing_scope": "new_rough|fixture_replace|none",
  "fire_protection": "sprinkler|alarm|suppression|none",
  "low_voltage": ["data", "security", "AV", "POS"],

  "gaming_area_sqft": <number or 0>,
  "terminal_count": <number or 0>,
  "gaming_electrical": true/false,
  "gaming_data": true/false,

  "site_work": ["<item>", ...],
  "parking_spaces": <number>,
  "fuel_canopy": true/false,
  "underground_tanks": true/false,

  "notes": "<anything else relevant to cost estimation>"
}

Be quantitative. If you can't determine a value from the documents, use reasonable estimates based on the property type and note your assumptions."""


FEASIBILITY_PROMPT = """You are a construction feasibility analyst assessing whether a project should proceed.

You have the full cost estimate, schedule, and manpower takeoff. Assess feasibility considering:
1. Total cost vs. budget and acquisition price
2. Construction duration vs. timeline requirements
3. Labor availability and trade requirements
4. Regulatory/permitting risks
5. Impact on investment returns (ROI/IRR)

You have tools available:
- web_search: Look up permit timelines, labor market conditions, material supply chain issues

Respond with ONLY a JSON object:
{
  "feasibility": "viable|marginal|not_viable",
  "feasibility_score": 0.0-1.0,
  "go_no_go": "GO|MODIFY|NO_GO",

  "cost_assessment": "<is total cost reasonable for scope?>",
  "schedule_assessment": "<is timeline achievable?>",
  "labor_assessment": "<can we find the trades needed?>",
  "regulatory_assessment": "<permitting risks?>",
  "roi_impact": "<how does construction cost affect deal returns?>",

  "risk_factors": ["<specific risk>", ...],
  "cost_saving_opportunities": ["<opportunity>", ...],
  "schedule_acceleration_options": ["<option>", ...],
  "recommendations": ["<actionable recommendation>", ...]
}"""


# ═══════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════

class ConstructionPipeline:
    """Full construction analysis pipeline."""

    def __init__(
        self,
        session=None,
        workspace_id: str = "",
        user_id: str = "",
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._llm_client = None
        self._search_handler = None

    def _ensure_llm(self):
        if self._llm_client is None and os.environ.get("ANTHROPIC_API_KEY"):
            from engine.strategic.llm_client import LLMClient
            self._llm_client = LLMClient()

    def _get_search_tool(self):
        """Build web_search tool for LLM use."""
        if self._search_handler is None:
            from engine.strategic.pipeline import build_tool_handlers
            handlers = build_tool_handlers(self._session, self._workspace_id, self._user_id)
            self._search_handler = handlers.get("web_search")

        if self._search_handler:
            from engine.strategic.llm_client import ToolDefinition
            return ToolDefinition(
                name="web_search",
                description="Search the web for construction costs, labor rates, permits, regulations",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
                handler=self._search_handler,
            )
        return None

    # ── Main Entry Points ─────────────────────────────────

    def analyze(
        self,
        scope: Optional[Dict] = None,
        document_text: str = "",
        address: str = "",
        state: str = "IL",
        city: str = "",
        budget: float = 0,
        acquisition_price: float = 0,
        noi: float = 0,
    ) -> Dict:
        """Run full construction analysis pipeline.

        Either provide scope dict directly or document_text for LLM extraction.

        Returns ConstructionAssessment as dict.
        """
        from engine.construction import ConstructionAssessment

        start = time.perf_counter()
        project_id = f"con_{uuid.uuid4().hex[:12]}"
        web_searches = 0

        # ── Step 1: Scope ─────────────────────────────────
        if scope:
            extracted_scope = scope
        elif document_text:
            self._ensure_llm()
            extracted_scope = self._extract_scope(document_text)
            if not extracted_scope.get("total_sqft"):
                extracted_scope["total_sqft"] = 2000  # reasonable default
        else:
            extracted_scope = {
                "project_name": address or "Unknown",
                "project_type": "renovation",
                "property_type": "gas_station",
                "total_sqft": 2000,
                "terminal_count": 6,
            }

        # Fill in address/state if provided
        if address:
            extracted_scope["address"] = address
        if not extracted_scope.get("project_name"):
            extracted_scope["project_name"] = address or "Construction Project"

        # ── Step 2: Cost Estimate ─────────────────────────
        from engine.construction.costs import estimate_costs
        cost_est = estimate_costs(
            extracted_scope, state=state, city=city,
            quality="mid",
        )

        # ── Step 3: Schedule ──────────────────────────────
        from engine.construction.schedule import build_schedule, manpower_takeoff
        schedule = build_schedule(extracted_scope)
        manpower = manpower_takeoff(schedule, state=state)

        # ── Step 4: Feasibility Assessment ────────────────
        feasibility = self._assess_feasibility(
            extracted_scope, cost_est, schedule, manpower,
            budget=budget, acquisition_price=acquisition_price, noi=noi,
        )

        elapsed = int((time.perf_counter() - start) * 1000)
        total_cost = cost_est.get("total_project_cost", 0)
        total_sqft = extracted_scope.get("total_sqft", 0)

        assessment = ConstructionAssessment(
            project_id=project_id,
            project_name=extracted_scope.get("project_name", ""),
            feasibility=feasibility.get("feasibility", "viable"),
            feasibility_score=feasibility.get("feasibility_score", 0.5),
            go_no_go=feasibility.get("go_no_go", "MODIFY"),
            total_project_cost=total_cost,
            cost_per_sqft=round(total_cost / total_sqft, 2) if total_sqft else 0,
            construction_duration_weeks=schedule.get("total_duration_weeks", 0),
            roi_impact=feasibility.get("roi_impact", ""),
            risk_factors=feasibility.get("risk_factors", []),
            recommendations=feasibility.get("recommendations", []),
            documents_analyzed=1 if document_text else 0,
            web_searches_run=web_searches,
            llm_cost_usd=self._llm_client.total_cost if self._llm_client else 0,
            elapsed_ms=elapsed,
        )

        result = assessment.to_dict()
        result["scope"] = extracted_scope
        result["cost_estimate"] = cost_est
        result["schedule"] = schedule
        result["manpower"] = manpower
        result["feasibility_details"] = feasibility

        # Store in vector DB for future comps
        self._store_historical(extracted_scope, cost_est, state, city)

        return result

    def quick_estimate(
        self,
        property_type: str = "gas_station",
        project_type: str = "renovation",
        sqft: float = 2000,
        terminal_count: int = 0,
        state: str = "IL",
        city: str = "",
    ) -> Dict:
        """Quick cost estimate without documents or LLM."""
        scope = {
            "project_type": project_type,
            "property_type": property_type,
            "total_sqft": sqft,
            "renovation_sqft": sqft if project_type != "new_build" else 0,
            "new_construction_sqft": sqft if project_type == "new_build" else 0,
            "terminal_count": terminal_count,
            "gaming_electrical": terminal_count > 0,
            "gaming_data": terminal_count > 0,
            "hvac_tons": max(1, sqft / 400),
            "electrical_service": "400A" if sqft > 3000 else "200A",
            "plumbing_fixtures": max(2, sqft // 500),
            "fire_protection": "sprinkler" if sqft > 2000 else "alarm",
        }
        return self.analyze(scope=scope, state=state, city=city)

    # ── Scope Extraction ──────────────────────────────────

    def _extract_scope(self, document_text: str) -> Dict:
        """Use LLM to extract construction scope from document text."""
        if not self._llm_client:
            return {}

        from engine.strategic.llm_client import parse_json_from_llm

        tools = []
        search_tool = self._get_search_tool()
        if search_tool:
            tools.append(search_tool)

        resp = self._llm_client.call(
            SCOPE_EXTRACTION_PROMPT,
            f"DOCUMENT TEXT:\n\n{document_text[:12000]}",
            route_tier="strategic_deep",
            tools=tools or None,
            temperature=0.1,
        )

        result = parse_json_from_llm(resp.text)
        if not result:
            result = {"project_type": "renovation", "total_sqft": 2000}

        return result

    # ── Feasibility Assessment ────────────────────────────

    def _assess_feasibility(
        self,
        scope: Dict, cost_est: Dict, schedule: Dict, manpower: Dict,
        budget: float = 0, acquisition_price: float = 0, noi: float = 0,
    ) -> Dict:
        """Assess construction feasibility — LLM or rule-based."""
        total_cost = cost_est.get("total_project_cost", 0)
        duration_weeks = schedule.get("total_duration_weeks", 0)
        peak_workers = schedule.get("peak_workers", 0)

        # Try LLM assessment first
        if self._llm_client:
            try:
                return self._llm_feasibility(
                    scope, cost_est, schedule, manpower,
                    budget, acquisition_price, noi,
                )
            except Exception as e:
                logger.warning(f"LLM feasibility failed: {e}")

        # Rule-based fallback
        return self._rule_feasibility(
            scope, cost_est, schedule, manpower,
            budget, acquisition_price, noi,
        )

    def _llm_feasibility(
        self,
        scope: Dict, cost_est: Dict, schedule: Dict, manpower: Dict,
        budget: float, acquisition_price: float, noi: float,
    ) -> Dict:
        """LLM-powered feasibility assessment."""
        from engine.strategic.llm_client import parse_json_from_llm

        context = f"""PROJECT SCOPE:
{json.dumps({k: v for k, v in scope.items() if k != 'documents_parsed'}, indent=2, default=str)}

COST ESTIMATE:
- Hard costs: ${cost_est.get('hard_cost_subtotal', 0):,.0f}
- Soft costs: ${cost_est.get('soft_cost_subtotal', 0):,.0f}
- Contingency: ${cost_est.get('contingency_total', 0):,.0f}
- TOTAL: ${cost_est.get('total_project_cost', 0):,.0f}
- Cost/sqft: ${cost_est.get('cost_per_sqft', 0):,.0f}

SCHEDULE:
- Construction duration: {schedule.get('total_duration_weeks', 0)} weeks ({schedule.get('total_duration_days', 0)} days)
- Peak workers: {schedule.get('peak_workers', 0)}
- Trades required: {', '.join(schedule.get('trades_required', []))}

MANPOWER:
- Total man-days: {manpower.get('total_man_days', 0)}
- Total labor cost: ${manpower.get('total_labor_cost', 0):,.0f}

FINANCIAL CONTEXT:
- Budget: ${budget:,.0f} (0 = not specified)
- Acquisition price: ${acquisition_price:,.0f} (0 = not specified)
- NOI: ${noi:,.0f}/yr (0 = not specified)
- Total all-in cost (acquisition + construction): ${(acquisition_price + cost_est.get('total_project_cost', 0)):,.0f}"""

        tools = []
        search_tool = self._get_search_tool()
        if search_tool:
            tools.append(search_tool)

        resp = self._llm_client.call(
            FEASIBILITY_PROMPT,
            context,
            route_tier="strategic_fast",
            tools=tools or None,
            temperature=0.2,
        )

        result = parse_json_from_llm(resp.text)
        if result.get("go_no_go") not in ("GO", "MODIFY", "NO_GO"):
            result["go_no_go"] = "MODIFY"
        return result

    def _rule_feasibility(
        self,
        scope: Dict, cost_est: Dict, schedule: Dict, manpower: Dict,
        budget: float, acquisition_price: float, noi: float,
    ) -> Dict:
        """Rule-based feasibility assessment."""
        total_cost = cost_est.get("total_project_cost", 0)
        duration_weeks = schedule.get("total_duration_weeks", 0)
        cost_sqft = cost_est.get("cost_per_sqft", 0)
        all_in = acquisition_price + total_cost if acquisition_price else total_cost

        risks = []
        recs = []
        score = 0.7  # start optimistic

        # Budget check
        if budget > 0 and total_cost > budget:
            overrun = (total_cost - budget) / budget
            if overrun > 0.3:
                risks.append(f"Construction cost ${total_cost:,.0f} exceeds budget ${budget:,.0f} by {overrun:.0%}")
                score -= 0.25
            else:
                risks.append(f"Construction cost slightly over budget ({overrun:.0%})")
                score -= 0.1
            recs.append("Value-engineer scope to reduce costs")

        # Duration check
        if duration_weeks > 24:
            risks.append(f"Long construction timeline ({duration_weeks} weeks)")
            score -= 0.1
            recs.append("Consider phased construction to start revenue earlier")

        # DSCR impact (if NOI and financing available)
        if noi > 0 and total_cost > 0:
            # Rough: if construction adds 20%+ to acquisition, it impacts DSCR
            if acquisition_price > 0:
                cost_ratio = total_cost / acquisition_price
                if cost_ratio > 0.3:
                    risks.append(f"Construction is {cost_ratio:.0%} of acquisition price")
                    score -= 0.1
                if cost_ratio > 0.5:
                    risks.append("Construction cost may make financing challenging")
                    score -= 0.15
                    recs.append("Explore SBA 504 or construction-to-perm loan")

        # Cost/sqft reasonableness
        property_type = scope.get("property_type", "gas_station")
        project_type = scope.get("project_type", "renovation")
        from engine.construction.costs import COST_PER_SQFT
        benchmarks = COST_PER_SQFT.get(project_type, {}).get(property_type, {"mid": 150})
        if cost_sqft > benchmarks.get("high", 300) * 1.2:
            risks.append(f"Cost/sqft (${cost_sqft:,.0f}) above market benchmark")
            score -= 0.1
            recs.append("Review scope for unnecessary premiums")

        # Manpower check
        peak = schedule.get("peak_workers", 0)
        if peak > 15:
            risks.append(f"High peak labor ({peak} workers) may cause coordination issues")
            score -= 0.05

        # Terminal count check
        terminals = scope.get("terminal_count", 0)
        if terminals > 0:
            recs.append("Ensure gaming board pre-approval before starting construction")
            if terminals > 6:
                recs.append("Verify municipality allows >6 terminals")

        score = max(0.1, min(0.95, score))

        if score >= 0.65:
            feasibility = "viable"
            go = "GO"
        elif score >= 0.4:
            feasibility = "marginal"
            go = "MODIFY"
        else:
            feasibility = "not_viable"
            go = "NO_GO"

        roi_impact = ""
        if noi > 0 and all_in > 0:
            cap_rate = noi / all_in
            roi_impact = f"All-in cap rate: {cap_rate:.1%} on ${all_in:,.0f}"

        return {
            "feasibility": feasibility,
            "feasibility_score": round(score, 2),
            "go_no_go": go,
            "cost_assessment": f"${total_cost:,.0f} total (${cost_sqft:,.0f}/sqft)",
            "schedule_assessment": f"{duration_weeks} weeks construction",
            "labor_assessment": f"Peak {peak} workers across {len(schedule.get('trades_required', []))} trades",
            "regulatory_assessment": "Standard permitting expected" if not any("permit" in r.lower() for r in risks) else "Permitting risks identified",
            "roi_impact": roi_impact,
            "risk_factors": risks,
            "cost_saving_opportunities": [],
            "schedule_acceleration_options": [],
            "recommendations": recs,
        }

    # ── Historical Storage ────────────────────────────────

    def _store_historical(self, scope: Dict, cost_est: Dict, state: str, city: str):
        """Store cost data in vector store for future comp lookups."""
        try:
            from engine.strategic.vector_store import VectorStore
            vs = VectorStore(self._workspace_id)
            vs.store_construction_cost(
                project_name=scope.get("project_name", ""),
                project_type=scope.get("project_type", "renovation"),
                total_cost=cost_est.get("total_project_cost", 0),
                sqft=scope.get("total_sqft", 0),
                location=city,
                state=state,
                details={
                    "property_type": scope.get("property_type", ""),
                    "terminal_count": scope.get("terminal_count", 0),
                    "cost_per_sqft": cost_est.get("cost_per_sqft", 0),
                    "hard_cost": cost_est.get("hard_cost_subtotal", 0),
                },
            )
        except Exception as e:
            logger.debug(f"Historical storage skipped: {e}")
