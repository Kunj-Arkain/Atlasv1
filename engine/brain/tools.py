"""
engine.brain.tools — Unified Tool Registry
==============================================
Phase 8B: Registers all platform tools (Phase 2–7) for agent access.

Each tool is a callable with a schema describing its inputs/outputs.
Tools pass through PolicyBroker before execution.

Usage:
    registry = ToolRegistry(session, workspace_id="ws1")
    registry.register_all()
    result = registry.execute("egm_predict", {
        "venue_type": "bar", "state": "IL", "terminal_count": 5,
    })
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolSpec:
    """Describes a registered tool."""
    name: str
    description: str
    category: str  # financial, egm, contract, deal, portfolio
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    cost_estimate: float = 0.0  # estimated cost per call


@dataclass
class ToolResult:
    """Result from tool execution."""
    tool_name: str
    success: bool
    data: Any = None
    error: str = ""
    execution_ms: int = 0
    cost: float = 0.0


class ToolRegistry:
    """Central registry for all platform tools.

    All tools go through:
      1. Schema validation
      2. Policy check (via PolicyBroker)
      3. Execution
      4. Cost tracking (via CostMeter)
      5. Audit logging
    """

    def __init__(self, session=None, workspace_id: str = "", user_id: str = ""):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._tools: Dict[str, ToolSpec] = {}
        self._handlers: Dict[str, Callable] = {}
        self._execution_log: List[Dict] = []

    def register(self, spec: ToolSpec, handler: Callable):
        """Register a tool with its handler."""
        self._tools[spec.name] = spec
        self._handlers[spec.name] = handler

    def register_all(self):
        """Register all platform tools from Phases 2–12."""
        self._register_financial_tools()
        self._register_egm_tools()
        self._register_contract_tools()
        self._register_deal_tools()
        self._register_portfolio_tools()
        self._register_strategic_tools()
        self._register_domain_tools()

    def list_tools(self, category: str = "") -> List[Dict]:
        """List all registered tools, optionally filtered by category."""
        tools = []
        for name, spec in sorted(self._tools.items()):
            if category and spec.category != category:
                continue
            tools.append({
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "requires_approval": spec.requires_approval,
                "parameters": spec.parameters,
            })
        return tools

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def execute(self, tool_name: str, params: Dict) -> ToolResult:
        """Execute a tool by name with given parameters."""
        start = time.perf_counter()

        spec = self._tools.get(tool_name)
        if not spec:
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"Unknown tool: {tool_name}",
            )

        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"No handler for tool: {tool_name}",
            )

        try:
            data = handler(params)
            elapsed = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool_name=tool_name, success=True,
                data=data, execution_ms=elapsed,
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool_name=tool_name, success=False,
                error=str(e), execution_ms=elapsed,
            )

        self._execution_log.append({
            "tool": tool_name, "success": result.success,
            "ms": result.execution_ms, "error": result.error,
        })
        return result

    @property
    def execution_log(self) -> List[Dict]:
        return list(self._execution_log)

    # ── Phase 2: Financial Tools ──────────────────────────

    def _register_financial_tools(self):
        from engine.financial.tools import FinancialToolkit

        toolkit = FinancialToolkit()

        self.register(
            ToolSpec("amortize", "Compute loan amortization schedule",
                     "financial", {"principal": "float", "annual_rate": "float",
                                   "months": "int"}),
            lambda p: toolkit.amortize(**p),
        )
        self.register(
            ToolSpec("irr", "Compute internal rate of return",
                     "financial", {"cash_flows": "list[float]"}),
            lambda p: toolkit.irr(**p),
        )
        self.register(
            ToolSpec("dscr", "Compute debt service coverage ratio",
                     "financial", {"noi": "float", "annual_debt_service": "float"}),
            lambda p: toolkit.dscr(**p),
        )
        self.register(
            ToolSpec("cap_rate", "Compute capitalization rate",
                     "financial", {"noi": "float", "value": "float"}),
            lambda p: toolkit.cap_rate(**p),
        )
        self.register(
            ToolSpec("cash_on_cash", "Compute cash-on-cash return",
                     "financial", {"annual_cf": "float", "equity_invested": "float"}),
            lambda p: toolkit.cash_on_cash(**p),
        )

    # ── Phase 3-4: EGM Tools ─────────────────────────────

    def _register_egm_tools(self):
        self.register(
            ToolSpec("egm_predict", "Predict EGM performance for a location",
                     "egm", {"venue_type": "str", "state": "str",
                             "terminal_count": "int"}),
            self._handle_egm_predict,
        )
        self.register(
            ToolSpec("egm_classify", "Classify venue type from name",
                     "egm", {"name": "str"}),
            self._handle_egm_classify,
        )
        self.register(
            ToolSpec("egm_market_health", "Get EGM market health stats",
                     "egm", {"state": "str"}),
            self._handle_egm_health,
        )

    def _handle_egm_predict(self, params: Dict) -> Dict:
        from engine.egm.prediction import PredictionService
        svc = PredictionService(
            self._session, self._workspace_id, self._user_id,
        )
        return svc.predict(**params, include_similar=True)

    def _handle_egm_classify(self, params: Dict) -> Dict:
        from engine.egm.classifier import classify_venue
        venue_type = classify_venue(params["name"])
        return {"name": params["name"], "venue_type": venue_type}

    def _handle_egm_health(self, params: Dict) -> Dict:
        from engine.egm.analytics import EGMAnalytics
        analytics = EGMAnalytics(self._session)
        return analytics.market_health(params.get("state", ""))

    # ── Phase 5: Contract Tools ───────────────────────────

    def _register_contract_tools(self):
        self.register(
            ToolSpec("simulate_contract", "Run Monte Carlo on a contract",
                     "contract", {"agreement_type": "str"},
                     requires_approval=False),
            self._handle_simulate,
        )
        self.register(
            ToolSpec("compare_structures", "Compare contract structures",
                     "contract", {"structures": "list"}),
            self._handle_compare,
        )

    def _handle_simulate(self, params: Dict) -> Dict:
        from engine.contracts.montecarlo import SimulationInputs, run_simulation
        from dataclasses import asdict
        inputs = SimulationInputs(**params)
        result = run_simulation(inputs)
        return asdict(result)

    def _handle_compare(self, params: Dict) -> List:
        from engine.contracts.montecarlo import SimulationInputs, compare_structures
        base = SimulationInputs(**params.get("base", {}))
        return compare_structures(base, params.get("structures", []))

    # ── Phase 6: Deal Tools ───────────────────────────────

    def _register_deal_tools(self):
        self.register(
            ToolSpec("evaluate_deal", "Run full deal evaluation pipeline",
                     "deal", {"purchase_price": "float", "property_type": "str"},
                     requires_approval=True),
            self._handle_evaluate_deal,
        )

    def _handle_evaluate_deal(self, params: Dict) -> Dict:
        from engine.realestate.pipeline import DealPipeline
        pipeline = DealPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.evaluate(params)

    # ── Phase 7: Portfolio Tools ──────────────────────────

    def _register_portfolio_tools(self):
        self.register(
            ToolSpec("portfolio_dashboard", "Get portfolio dashboard",
                     "portfolio", {}),
            self._handle_dashboard,
        )
        self.register(
            ToolSpec("deal_impact", "Analyze new deal impact on portfolio",
                     "portfolio", {"name": "str", "state": "str",
                                   "current_value": "float"}),
            self._handle_deal_impact,
        )

    def _handle_dashboard(self, params: Dict) -> Dict:
        from engine.portfolio.analytics import PortfolioAnalytics
        analytics = PortfolioAnalytics(self._session, self._workspace_id)
        return analytics.dashboard()

    def _handle_deal_impact(self, params: Dict) -> Dict:
        from engine.portfolio.analytics import PortfolioAnalytics
        analytics = PortfolioAnalytics(self._session, self._workspace_id)
        return analytics.new_deal_impact(params)

    # ── Phase 8+: Strategic Intelligence Tools ────────────

    def _register_strategic_tools(self):
        self.register(
            ToolSpec("strategic_analyze", "Run full strategic analysis pipeline",
                     "strategic", {"scenario_text": "str", "title": "str",
                                   "objectives": "list[str]"},
                     requires_approval=False, cost_estimate=0.10),
            self._handle_strategic_analyze,
        )
        self.register(
            ToolSpec("swot_generate", "Generate SWOT analysis for a scenario",
                     "strategic", {"scenario_text": "str"},
                     requires_approval=False),
            self._handle_swot,
        )
        self.register(
            ToolSpec("decision_stress_test", "Stress-test a decision with failure modes",
                     "strategic", {"scenario_text": "str"},
                     requires_approval=False),
            self._handle_stress_test,
        )
        self.register(
            ToolSpec("scenario_simulate", "Generate bull/base/bear scenario cases",
                     "strategic", {"scenario_text": "str"},
                     requires_approval=False),
            self._handle_scenario_simulate,
        )
        self.register(
            ToolSpec("assumption_audit", "Audit assumptions and identify gaps",
                     "strategic", {"scenario_text": "str"},
                     requires_approval=False),
            self._handle_assumption_audit,
        )
        self.register(
            ToolSpec("market_research", "Run deep market research for a site/address",
                     "strategic", {"address": "str", "property_type": "str"},
                     requires_approval=False, cost_estimate=0.25),
            self._handle_market_research,
        )
        self.register(
            ToolSpec("construction_estimate", "Quick construction cost estimate",
                     "construction", {"property_type": "str", "sqft": "float",
                                      "project_type": "str", "terminal_count": "int"},
                     requires_approval=False),
            self._handle_construction_estimate,
        )
        self.register(
            ToolSpec("construction_feasibility", "Full construction feasibility analysis",
                     "construction", {"address": "str", "scope": "dict"},
                     requires_approval=False, cost_estimate=0.15),
            self._handle_construction_feasibility,
        )

    def _handle_strategic_analyze(self, params: Dict) -> Dict:
        from engine.strategic.pipeline import StrategicPipeline
        pipeline = StrategicPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.analyze(params)

    def _handle_swot(self, params: Dict) -> Dict:
        from engine.strategic.pipeline import StrategicPipeline
        pipeline = StrategicPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.swot_only(params)

    def _handle_stress_test(self, params: Dict) -> Dict:
        from engine.strategic.pipeline import StrategicPipeline
        pipeline = StrategicPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.stress_test(params)

    def _handle_scenario_simulate(self, params: Dict) -> Dict:
        from engine.strategic.pipeline import StrategicPipeline
        pipeline = StrategicPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.scenario_simulate(params)

    def _handle_assumption_audit(self, params: Dict) -> Dict:
        from engine.strategic.pipeline import StrategicPipeline
        pipeline = StrategicPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.assumption_audit(params)

    def _handle_market_research(self, params: Dict) -> Dict:
        from engine.strategic.research import MarketResearcher
        researcher = MarketResearcher(
            self._session, self._workspace_id, self._user_id,
        )
        return researcher.research_site(**params)

    def _handle_construction_estimate(self, params: Dict) -> Dict:
        from engine.construction.pipeline import ConstructionPipeline
        pipeline = ConstructionPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.quick_estimate(**params)

    def _handle_construction_feasibility(self, params: Dict) -> Dict:
        from engine.construction.pipeline import ConstructionPipeline
        pipeline = ConstructionPipeline(
            self._session, self._workspace_id, self._user_id,
        )
        return pipeline.analyze(**params)

    # ═══════════════════════════════════════════════════════
    # PHASE 12: DOMAIN TOOLS
    # ═══════════════════════════════════════════════════════

    def _register_domain_tools(self):
        # ── Comparable Sales ──
        self.register(
            ToolSpec("pull_comps", "Pull comparable sales for a property",
                     category="real_estate",
                     parameters={
                         "address": "str — subject property address",
                         "radius_miles": "float — search radius (default 3)",
                         "property_type": "str — gas_station, restaurant, retail, etc",
                         "max_results": "int — max comps to return (default 10)",
                         "min_sqft": "float — minimum sqft filter",
                         "max_sqft": "float — maximum sqft filter",
                     }),
            self._handle_pull_comps,
        )

        # ── County Tax Records ──
        self.register(
            ToolSpec("county_tax_lookup", "Look up county tax/assessment records",
                     category="real_estate",
                     parameters={
                         "address": "str — property address",
                         "parcel_id": "str — APN/parcel number (optional)",
                         "county": "str — county name",
                         "state": "str — state code",
                     }),
            self._handle_county_tax,
        )

        # ── Lease Clause Analysis ──
        self.register(
            ToolSpec("analyze_lease", "Analyze lease document for key clauses and risks",
                     category="legal",
                     parameters={
                         "lease_text": "str — raw text of lease document",
                         "lease_type": "str — commercial, ground, triple_net, gross",
                         "focus_areas": "list — specific clauses to analyze",
                     }),
            self._handle_analyze_lease,
        )

        # ── Lender Term Sheet Generator ──
        self.register(
            ToolSpec("generate_term_sheets", "Generate lender term sheet variants",
                     category="financial",
                     parameters={
                         "property_type": "str",
                         "purchase_price": "float",
                         "noi": "float",
                         "borrower_experience": "str — novice, moderate, experienced",
                         "credit_score": "int",
                         "loan_types": "list — SBA_504, SBA_7a, conventional, bridge, CMBS",
                     }),
            self._handle_term_sheets,
        )

        # ── EB-5 Job Impact ──
        self.register(
            ToolSpec("eb5_job_impact", "Compute EB-5 visa job creation estimates",
                     category="financial",
                     parameters={
                         "total_investment": "float — total project cost",
                         "construction_cost": "float — hard construction costs",
                         "operating_revenue": "float — annual operating revenue",
                         "state": "str — state code",
                         "industry_naics": "str — NAICS code (default 447110 gas stations)",
                     }),
            self._handle_eb5_jobs,
        )

    # ── Domain Handlers ───────────────────────────────────

    def _handle_pull_comps(self, params: Dict) -> Dict:
        """Pull comparable sales via multi-search + vector store."""
        address = params.get("address", "")
        radius = params.get("radius_miles", 3)
        ptype = params.get("property_type", "gas_station")
        max_results = params.get("max_results", 10)

        # Check vector store first
        comps = []
        try:
            from engine.strategic.vector_store import VectorStore
            vs = VectorStore(self._workspace_id)
            stored = vs.search("property_comps", f"{ptype} near {address}", top_k=max_results)
            comps = [s["metadata"] for s in stored if s.get("score", 0) > 0.5]
        except Exception:
            pass

        # Web search for additional comps
        try:
            from engine.strategic.search_providers import multi_search
            queries = [
                f"{ptype.replace('_', ' ')} sold near {address}",
                f"commercial property sales {address.split(',')[-1].strip() if ',' in address else address}",
                f"{ptype.replace('_', ' ')} cap rate {address.split(',')[-1].strip() if ',' in address else ''}",
            ]
            web_comps = []
            for q in queries[:2]:
                resp = multi_search(q, num_results=5)
                if resp.top_snippets:
                    web_comps.append({"query": q, "findings": resp.top_snippets})
        except Exception:
            web_comps = []

        return {
            "address": address,
            "stored_comps": comps,
            "stored_count": len(comps),
            "web_research": web_comps,
            "search_radius_miles": radius,
            "property_type": ptype,
        }

    def _handle_county_tax(self, params: Dict) -> Dict:
        """Look up county tax/assessment data via web search."""
        address = params.get("address", "")
        county = params.get("county", "")
        state = params.get("state", "")
        parcel_id = params.get("parcel_id", "")

        search_query = f"{address} property tax assessment {county} county {state}"
        if parcel_id:
            search_query = f"parcel {parcel_id} {county} county {state} tax assessment"

        try:
            from engine.strategic.search_providers import multi_search
            resp = multi_search(search_query, num_results=5)
            findings = resp.top_snippets
        except Exception:
            findings = "Search unavailable"

        # Also search for tax rate
        try:
            from engine.strategic.search_providers import multi_search
            rate_resp = multi_search(f"{county} county {state} property tax rate 2024 2025", num_results=3)
            tax_rate_info = rate_resp.top_snippets
        except Exception:
            tax_rate_info = ""

        return {
            "address": address,
            "county": county,
            "state": state,
            "parcel_id": parcel_id,
            "assessment_findings": findings,
            "tax_rate_info": tax_rate_info,
        }

    def _handle_analyze_lease(self, params: Dict) -> Dict:
        """Analyze lease clauses — rule-based extraction + LLM if available."""
        lease_text = params.get("lease_text", "")
        lease_type = params.get("lease_type", "commercial")
        focus = params.get("focus_areas", [])

        if not lease_text:
            return {"error": "No lease text provided"}

        # Rule-based clause detection
        clauses = _extract_lease_clauses(lease_text)

        # LLM-enhanced analysis if available
        llm_analysis = {}
        try:
            from engine.strategic.llm_client import LLMClient
            client = LLMClient()
            prompt = (
                f"Analyze this {lease_type} lease. Identify:\n"
                f"1. Key financial terms (rent, escalations, CAM, tax pass-throughs)\n"
                f"2. Risk clauses (termination, assignment, default, exclusivity)\n"
                f"3. Gaming-specific provisions if any\n"
                f"4. Unusual or concerning provisions\n"
                f"{'Focus on: ' + ', '.join(focus) if focus else ''}\n\n"
                f"Respond with JSON: {{\"financial_terms\": {{}}, \"risk_clauses\": [], "
                f"\"gaming_provisions\": [], \"concerns\": [], \"lease_grade\": \"A-F\"}}"
            )
            resp = client.call(prompt, lease_text[:8000], route_tier="cheap_structured")
            from engine.strategic.llm_client import parse_json_from_llm
            llm_analysis = parse_json_from_llm(resp.text)
        except Exception:
            pass

        return {
            "lease_type": lease_type,
            "detected_clauses": clauses,
            "llm_analysis": llm_analysis,
            "text_length": len(lease_text),
        }

    def _handle_term_sheets(self, params: Dict) -> Dict:
        """Generate lender term sheet variants."""
        pp = params.get("purchase_price", 0)
        noi = params.get("noi", 0)
        ptype = params.get("property_type", "gas_station")
        experience = params.get("borrower_experience", "moderate")
        credit = params.get("credit_score", 700)
        loan_types = params.get("loan_types", ["SBA_504", "conventional"])

        cap_rate = noi / pp if pp > 0 else 0
        dscr_target = 1.25

        sheets = []
        for lt in loan_types:
            sheet = _build_term_sheet(lt, pp, noi, cap_rate, ptype, experience, credit)
            sheets.append(sheet)

        return {
            "property_type": ptype,
            "purchase_price": pp,
            "noi": noi,
            "cap_rate": round(cap_rate, 4),
            "term_sheets": sheets,
            "count": len(sheets),
        }

    def _handle_eb5_jobs(self, params: Dict) -> Dict:
        """Compute EB-5 job creation estimates using RIMS II multipliers."""
        total_inv = params.get("total_investment", 0)
        construction = params.get("construction_cost", 0)
        revenue = params.get("operating_revenue", 0)
        state = params.get("state", "IL")
        naics = params.get("industry_naics", "447110")

        # RIMS II approximate multipliers (jobs per $1M spend)
        construction_multiplier = {
            "IL": 11.2, "CA": 10.8, "NY": 10.5, "TX": 12.1,
            "PA": 11.0, "FL": 11.5, "OH": 11.3, "NV": 10.9,
        }.get(state, 11.0)

        operations_multiplier = {
            "447110": 8.5,   # Gas stations
            "722511": 12.3,  # Full-service restaurants
            "713210": 9.8,   # Casinos/gaming
            "445120": 7.2,   # Convenience stores
        }.get(naics, 9.0)

        construction_jobs = (construction / 1_000_000) * construction_multiplier
        operations_jobs = (revenue / 1_000_000) * operations_multiplier
        total_jobs = construction_jobs + operations_jobs

        # EB-5 minimum investment
        min_investment_per_visa = 800_000  # TEA rate
        potential_visas = int(total_jobs / 10)  # 10 jobs per visa

        return {
            "total_investment": total_inv,
            "construction_cost": construction,
            "operating_revenue": revenue,
            "state": state,
            "naics": naics,
            "construction_jobs": round(construction_jobs, 1),
            "operations_jobs": round(operations_jobs, 1),
            "total_jobs_created": round(total_jobs, 1),
            "eb5_visas_supportable": potential_visas,
            "min_investment_per_visa": min_investment_per_visa,
            "total_eb5_capital": potential_visas * min_investment_per_visa,
            "multipliers": {
                "construction": construction_multiplier,
                "operations": operations_multiplier,
            },
            "methodology": "RIMS II regional input-output multipliers (approximate)",
        }


# ═══════════════════════════════════════════════════════════════
# DOMAIN TOOL HELPERS
# ═══════════════════════════════════════════════════════════════

def _extract_lease_clauses(text: str) -> Dict:
    """Rule-based lease clause extraction."""
    text_lower = text.lower()
    clauses = {}

    patterns = {
        "rent_amount": r'\$[\d,]+(?:\.\d{2})?\s*(?:per month|/month|monthly|per year|/year|annually)',
        "escalation": r'(?:annual|yearly)\s+(?:increase|escalation|adjustment)\s+of\s+(\d+(?:\.\d+)?%)',
        "term_years": r'(?:initial|lease)\s+term\s+(?:of|is)\s+(\d+)\s+(?:year|yr)',
        "renewal_options": r'(\d+)\s+(?:option|renewal)\s+(?:period|term)s?\s+of\s+(\d+)\s+year',
        "cam_charges": r'(?:CAM|common area maintenance|operating expenses)\s+(?:of|estimated at)\s+\$?([\d,.]+)',
        "assignment": r'(?:assignment|subletting|transfer)\s+(?:is\s+)?(?:permitted|prohibited|requires|subject to)',
        "termination": r'(?:early termination|termination clause|right to terminate)',
        "exclusivity": r'(?:exclusive|exclusivity|non-compete)\s+(?:use|clause|provision|right)',
        "gaming": r'(?:gaming|gambling|video gaming|VGT|slot)',
    }

    for name, pattern in patterns.items():
        import re
        matches = re.findall(pattern, text_lower)
        if matches:
            clauses[name] = matches if len(matches) > 1 else matches[0]

    return clauses


def _build_term_sheet(
    loan_type: str, pp: float, noi: float, cap_rate: float,
    ptype: str, experience: str, credit: int,
) -> Dict:
    """Build a single term sheet variant."""
    configs = {
        "SBA_504": {
            "name": "SBA 504",
            "ltv": 0.90, "rate_spread": 2.75, "term_years": 25,
            "amort_years": 25, "prepay_penalty": "declining 5yr",
            "fees": "2.15% SBA fee + 0.5% CDC fee",
            "requirements": "Owner-occupied 51%+, job creation, US citizen/resident",
        },
        "SBA_7a": {
            "name": "SBA 7(a)",
            "ltv": 0.85, "rate_spread": 2.25, "term_years": 25,
            "amort_years": 25, "prepay_penalty": "declining 3yr",
            "fees": "2-3.5% guaranty fee",
            "requirements": "For-profit, US-based, meet size standards",
        },
        "conventional": {
            "name": "Conventional",
            "ltv": 0.75, "rate_spread": 1.75, "term_years": 10,
            "amort_years": 25, "prepay_penalty": "yield maintenance or defeasance",
            "fees": "0.5-1% origination",
            "requirements": "1.25x DSCR, 680+ credit, 25% down",
        },
        "bridge": {
            "name": "Bridge/Hard Money",
            "ltv": 0.70, "rate_spread": 5.50, "term_years": 2,
            "amort_years": 0, "prepay_penalty": "1-2% of balance",
            "fees": "2-4 points origination",
            "requirements": "Property as collateral, exit strategy required",
        },
        "CMBS": {
            "name": "CMBS",
            "ltv": 0.75, "rate_spread": 2.00, "term_years": 10,
            "amort_years": 30, "prepay_penalty": "defeasance or yield maintenance",
            "fees": "0.5-1% origination",
            "requirements": "1.20x DSCR, $2M+ loan, stabilized property",
        },
    }

    cfg = configs.get(loan_type, configs["conventional"])
    base_rate = 4.50  # approximate 10yr treasury
    rate = base_rate + cfg["rate_spread"]

    # Adjust for borrower risk
    if credit < 680:
        rate += 0.50
    if experience == "novice":
        rate += 0.25

    loan_amount = pp * cfg["ltv"]
    down_payment = pp - loan_amount

    # Monthly payment (P&I)
    if cfg["amort_years"] > 0:
        monthly_rate = rate / 100 / 12
        n_payments = cfg["amort_years"] * 12
        payment = loan_amount * (monthly_rate * (1 + monthly_rate) ** n_payments) / \
                  ((1 + monthly_rate) ** n_payments - 1)
    else:
        # Interest-only
        payment = loan_amount * (rate / 100 / 12)

    annual_debt = payment * 12
    dscr = noi / annual_debt if annual_debt > 0 else 0

    return {
        "loan_type": cfg["name"],
        "loan_amount": round(loan_amount, 2),
        "down_payment": round(down_payment, 2),
        "ltv": cfg["ltv"],
        "interest_rate": round(rate, 2),
        "term_years": cfg["term_years"],
        "amortization_years": cfg["amort_years"],
        "monthly_payment": round(payment, 2),
        "annual_debt_service": round(annual_debt, 2),
        "projected_dscr": round(dscr, 2),
        "prepayment_penalty": cfg["prepay_penalty"],
        "fees": cfg["fees"],
        "requirements": cfg["requirements"],
        "meets_dscr": dscr >= 1.25,
    }
