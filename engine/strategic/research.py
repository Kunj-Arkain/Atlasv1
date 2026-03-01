"""
engine.strategic.research — Deep Market Research Engine
==========================================================
Orchestrates multi-query web research to produce comprehensive
market reports for acquisition targets, gaming sites, and
strategic decisions.

Architecture:
  1. Takes a research brief (address, property type, objectives)
  2. Generates targeted search queries across 8 research domains
  3. Executes searches via Anthropic web_search tool
  4. Synthesizes findings into structured report via LLM
  5. Returns JSON report + optional markdown memo

Research domains:
  - Demographics (population, income, growth)
  - Traffic & accessibility
  - Competition (nearby similar businesses)
  - Gaming market (state/local terminal data, NTI trends)
  - Real estate comps (recent sales, valuations)
  - Regulatory environment (zoning, gaming regs)
  - Economic indicators (employment, development)
  - Risk factors (crime, environmental, market threats)

Usage:
    researcher = MarketResearcher(session, workspace_id)
    report = researcher.research_site(
        address="456 Oak Ave, Springfield, IL",
        property_type="gas_station",
        context="Evaluating for gaming terminal placement",
    )
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# RESEARCH QUERY TEMPLATES
# ═══════════════════════════════════════════════════════════════

def _build_search_queries(
    address: str,
    city: str,
    state: str,
    county: str,
    property_type: str,
    context: str,
) -> List[Dict[str, str]]:
    """Generate targeted search queries across research domains."""

    queries = []

    # ── Demographics ──────────────────────────────────────
    queries.append({
        "domain": "demographics",
        "query": f"{city} {state} population demographics income median",
        "purpose": "Population size, median household income, growth trends",
    })
    queries.append({
        "domain": "demographics",
        "query": f"{city} {state} population growth rate 2024 2025",
        "purpose": "Recent population growth trends",
    })

    # ── Traffic & Location ────────────────────────────────
    queries.append({
        "domain": "traffic",
        "query": f"{address} traffic count AADT vehicles per day",
        "purpose": "Daily traffic volume at or near site",
    })
    queries.append({
        "domain": "traffic",
        "query": f"{address} nearby interstate highway access",
        "purpose": "Proximity to major roads and highways",
    })

    # ── Competition ───────────────────────────────────────
    if property_type in ("gas_station", "convenience_store"):
        queries.append({
            "domain": "competition",
            "query": f"gas stations near {address} {city} {state}",
            "purpose": "Competing gas stations in immediate area",
        })
    queries.append({
        "domain": "competition",
        "query": f"{property_type.replace('_', ' ')} businesses near {city} {state}",
        "purpose": "Similar businesses in the market",
    })

    # ── Gaming Market (if relevant) ───────────────────────
    gaming_keywords = ["gaming", "terminal", "VGT", "slot", "EGM", "casino"]
    is_gaming = any(kw.lower() in context.lower() for kw in gaming_keywords) or \
                any(kw.lower() in property_type.lower() for kw in gaming_keywords)

    if is_gaming or state in ("IL", "PA", "WV", "MT", "NV", "LA", "OR", "SD"):
        queries.append({
            "domain": "gaming",
            "query": f"{state} video gaming terminal revenue {city} 2024 2025",
            "purpose": "Local gaming terminal revenue data and trends",
        })
        queries.append({
            "domain": "gaming",
            "query": f"{state} gaming board terminal locations {county} county",
            "purpose": "Number of gaming locations and terminals in area",
        })
        queries.append({
            "domain": "gaming",
            "query": f"{state} video gaming terminal regulations changes 2025",
            "purpose": "Recent regulatory changes affecting gaming",
        })
        queries.append({
            "domain": "gaming",
            "query": f"{city} {state} gaming competition terminals per capita",
            "purpose": "Gaming market saturation metrics",
        })

    # ── Real Estate Comps ─────────────────────────────────
    queries.append({
        "domain": "real_estate",
        "query": f"{property_type.replace('_', ' ')} sold {city} {state} price 2024 2025",
        "purpose": "Recent comparable property sales",
    })
    queries.append({
        "domain": "real_estate",
        "query": f"commercial real estate cap rate {city} {state}",
        "purpose": "Local cap rates and valuation benchmarks",
    })

    # ── Regulatory ────────────────────────────────────────
    queries.append({
        "domain": "regulatory",
        "query": f"{city} {state} zoning commercial {property_type.replace('_', ' ')}",
        "purpose": "Zoning and land use regulations",
    })
    queries.append({
        "domain": "regulatory",
        "query": f"{state} liquor license gaming license requirements {property_type.replace('_', ' ')}",
        "purpose": "License requirements for operation",
    })

    # ── Economic Indicators ───────────────────────────────
    queries.append({
        "domain": "economic",
        "query": f"{city} {state} unemployment rate economic development 2025",
        "purpose": "Local economic health indicators",
    })
    queries.append({
        "domain": "economic",
        "query": f"{city} {state} new construction development projects",
        "purpose": "Upcoming development that could affect traffic/demand",
    })

    # ── Risk Factors ──────────────────────────────────────
    queries.append({
        "domain": "risk",
        "query": f"{city} {state} crime rate safety statistics",
        "purpose": "Area safety profile",
    })

    return queries


# ═══════════════════════════════════════════════════════════════
# SYNTHESIS PROMPTS
# ═══════════════════════════════════════════════════════════════

RESEARCH_SYNTHESIS_PROMPT = """You are a senior market research analyst producing a comprehensive site analysis report.

You will receive raw search results from multiple research queries across these domains:
demographics, traffic, competition, gaming, real_estate, regulatory, economic, risk.

Synthesize ALL findings into a structured JSON report. Be specific with numbers, cite data points, and flag where data is missing or uncertain.

Respond with ONLY a JSON object matching this schema:
{
  "executive_summary": "<3-4 sentence overview of the site opportunity>",
  "site_score": 1-10,
  "site_grade": "A|B|C|D|F",

  "demographics": {
    "population": "<city/metro population>",
    "median_income": "<median household income>",
    "growth_trend": "<growing/stable/declining>",
    "key_facts": ["<fact 1>", ...]
  },

  "traffic_access": {
    "estimated_daily_traffic": "<if found>",
    "highway_proximity": "<nearest major roads>",
    "accessibility_score": 1-10,
    "key_facts": ["<fact>", ...]
  },

  "competition": {
    "direct_competitors_nearby": <count if found>,
    "competitor_names": ["<name>", ...],
    "market_saturation": "low|moderate|high|oversaturated",
    "key_facts": ["<fact>", ...]
  },

  "gaming_market": {
    "state_gaming_revenue_trend": "<growing/stable/declining>",
    "local_terminal_count": "<if found>",
    "avg_nti_per_terminal": "<if found>",
    "regulatory_outlook": "<favorable/neutral/unfavorable>",
    "key_facts": ["<fact>", ...]
  },

  "real_estate": {
    "comparable_sales": ["<comp 1>", ...],
    "estimated_cap_rate": "<local cap rate if found>",
    "valuation_assessment": "<appears fair/overpriced/underpriced>",
    "key_facts": ["<fact>", ...]
  },

  "regulatory": {
    "zoning_status": "<compatible/unknown/potential_issue>",
    "license_requirements": ["<requirement>", ...],
    "key_facts": ["<fact>", ...]
  },

  "economic_outlook": {
    "employment_trend": "<growing/stable/declining>",
    "development_activity": "<high/moderate/low>",
    "key_facts": ["<fact>", ...]
  },

  "risk_factors": [
    {"risk": "<description>", "severity": "high|medium|low", "mitigation": "<suggestion>"}
  ],

  "data_gaps": ["<what we couldn't find>", ...],
  "recommendations": ["<actionable recommendation>", ...],
  "sources_consulted": <number of search queries run>
}

Be thorough but honest about data quality. If a search returned no useful data for a domain, say so in data_gaps."""


# ═══════════════════════════════════════════════════════════════
# RESEARCH ENGINE
# ═══════════════════════════════════════════════════════════════

class MarketResearcher:
    """Deep market research via multi-query web search + LLM synthesis."""

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

    def _ensure_llm(self):
        if self._llm_client is None:
            from engine.strategic.llm_client import LLMClient
            self._llm_client = LLMClient()

    def research_site(
        self,
        address: str,
        property_type: str = "gas_station",
        context: str = "",
        city: str = "",
        state: str = "",
        county: str = "",
        purchase_price: float = 0,
        noi: float = 0,
        terminal_count: int = 0,
    ) -> Dict[str, Any]:
        """Run comprehensive market research for a site.

        Args:
            address: Property address
            property_type: gas_station, bar, restaurant, truck_stop, etc.
            context: Additional context (e.g. "evaluating for gaming expansion")
            city/state/county: Location details (auto-parsed from address if blank)
            purchase_price: Asking price (for valuation context)
            noi: Net operating income (for cap rate context)
            terminal_count: Number of gaming terminals (if applicable)

        Returns:
            Structured research report dict
        """
        self._ensure_llm()
        start = time.perf_counter()

        # Parse location from address if not provided
        if not city or not state:
            city, state, county = _parse_location(address)

        # Build research queries
        queries = _build_search_queries(
            address, city, state, county, property_type, context,
        )
        logger.info(f"Market research: {len(queries)} queries for {address}")

        # Check vector store for previous research on similar sites
        prior_research = []
        try:
            from engine.strategic.vector_store import VectorStore
            vs = VectorStore(self._workspace_id)
            prior_research = vs.find_similar_sites(address, top_k=3)
            if prior_research:
                logger.info(f"Found {len(prior_research)} prior research reports for context")
        except Exception as e:
            logger.debug(f"Vector store lookup skipped: {e}")

        # Execute all searches
        search_results = self._execute_searches(queries)

        # Add context about the property
        property_context = {
            "address": address,
            "property_type": property_type,
            "city": city,
            "state": state,
            "county": county,
            "purchase_price": purchase_price,
            "noi": noi,
            "terminal_count": terminal_count,
            "context": context,
            "prior_research_count": len(prior_research),
        }

        # Synthesize via LLM
        report = self._synthesize(search_results, property_context)

        elapsed = int((time.perf_counter() - start) * 1000)
        report["_meta"] = {
            "address": address,
            "city": city,
            "state": state,
            "property_type": property_type,
            "queries_executed": len(queries),
            "prior_research_found": len(prior_research),
            "elapsed_ms": elapsed,
            "llm_cost_usd": self._llm_client.total_cost if self._llm_client else 0,
        }

        # Store in vector DB for future lookups
        try:
            from engine.strategic.vector_store import VectorStore
            vs = VectorStore(self._workspace_id)
            vs.store_research(address, report)

            # Also store individual trend data points if found
            demo = report.get("demographics", {})
            if demo.get("median_income"):
                vs.store_trend_point("median_income", 0, city, state,
                                     source="market_research")
            gaming = report.get("gaming_market", {})
            if gaming.get("avg_nti_per_terminal"):
                vs.store_trend_point("avg_nti", 0, city, state,
                                     source="market_research")
        except Exception as e:
            logger.debug(f"Vector store write skipped: {e}")

        # Persist if session available
        if self._session:
            self._persist_report(address, report)

        return report

    def _execute_searches(self, queries: List[Dict]) -> List[Dict]:
        """Execute searches via multi-provider engine (Serper + Anthropic + Google)."""
        from engine.strategic.search_providers import multi_search

        results = []
        for q in queries:
            try:
                # Multi-provider search — uses all available keys
                resp = multi_search(
                    query=q["query"],
                    num_results=8,
                    search_type="search",
                )

                findings = resp.top_snippets
                if not findings:
                    findings = "No results found"

                results.append({
                    "domain": q["domain"],
                    "query": q["query"],
                    "purpose": q["purpose"],
                    "findings": findings,
                    "providers": resp.providers_used,
                    "result_count": resp.total_results,
                    "knowledge_panel": resp.knowledge_panel,
                    "success": resp.total_results > 0,
                })

            except Exception as e:
                logger.warning(f"Search failed for '{q['query']}': {e}")
                results.append({
                    "domain": q["domain"],
                    "query": q["query"],
                    "purpose": q["purpose"],
                    "findings": f"Search failed: {str(e)[:100]}",
                    "success": False,
                })

        return results

    def _synthesize(self, search_results: List[Dict], property_context: Dict) -> Dict:
        """Synthesize all search results into structured report."""
        from engine.strategic.llm_client import parse_json_from_llm

        # Group results by domain
        by_domain = {}
        for r in search_results:
            domain = r["domain"]
            if domain not in by_domain:
                by_domain[domain] = []
            by_domain[domain].append(r)

        # Build context for synthesis
        context_parts = [
            f"PROPERTY: {property_context['address']}",
            f"TYPE: {property_context['property_type']}",
            f"CITY/STATE: {property_context['city']}, {property_context['state']}",
        ]
        if property_context.get("purchase_price"):
            context_parts.append(f"ASKING PRICE: ${property_context['purchase_price']:,.0f}")
        if property_context.get("noi"):
            context_parts.append(f"NOI: ${property_context['noi']:,.0f}/yr")
        if property_context.get("terminal_count"):
            context_parts.append(f"GAMING TERMINALS: {property_context['terminal_count']}")
        if property_context.get("context"):
            context_parts.append(f"CONTEXT: {property_context['context']}")

        # Format search results
        findings_text = "\n\n".join(context_parts)
        findings_text += "\n\n" + "=" * 60 + "\nSEARCH RESULTS BY DOMAIN\n" + "=" * 60

        for domain, results in by_domain.items():
            findings_text += f"\n\n## {domain.upper()}\n"
            for r in results:
                findings_text += f"\nQuery: {r['query']}\nPurpose: {r['purpose']}\nFindings: {r['findings']}\n"

        resp = self._llm_client.call(
            RESEARCH_SYNTHESIS_PROMPT,
            findings_text,
            route_tier="strategic_deep",
            tools=None,
            temperature=0.2,
        )

        report = parse_json_from_llm(resp.text)
        if not report.get("executive_summary"):
            report["executive_summary"] = "Research synthesis incomplete. Review raw findings."
            report["data_gaps"] = report.get("data_gaps", []) + ["Synthesis may be incomplete"]

        report["sources_consulted"] = len(search_results)
        return report

    def _persist_report(self, address: str, report: Dict):
        """Save research report as a strategic artifact."""
        try:
            from engine.db.strategic_repositories import StrategicArtifactRepo
            repo = StrategicArtifactRepo(self._session)
            repo.create(
                run_id=f"research_{hash(address) % 100000:05d}",
                artifact_type="market_research",
                path=json.dumps(report),
            )
        except Exception as e:
            logger.warning(f"Failed to persist research report: {e}")


# ═══════════════════════════════════════════════════════════════
# REPORT FORMATTING
# ═══════════════════════════════════════════════════════════════

def format_research_markdown(report: Dict) -> str:
    """Convert research report dict to formatted markdown memo."""
    meta = report.get("_meta", {})
    address = meta.get("address", "Unknown")

    lines = [
        f"# Market Research Report: {address}",
        "",
        f"**Site Score: {report.get('site_score', '?')}/10** | "
        f"**Grade: {report.get('site_grade', '?')}** | "
        f"**Queries: {report.get('sources_consulted', 0)}** | "
        f"**Time: {meta.get('elapsed_ms', 0)}ms**",
        "",
        f"> {report.get('executive_summary', 'No summary available')}",
        "",
    ]

    # Demographics
    demo = report.get("demographics", {})
    if demo:
        lines += [
            "## Demographics", "",
            f"- **Population:** {demo.get('population', 'Unknown')}",
            f"- **Median Income:** {demo.get('median_income', 'Unknown')}",
            f"- **Growth Trend:** {demo.get('growth_trend', 'Unknown')}",
        ]
        for f in demo.get("key_facts", []):
            lines.append(f"- {f}")
        lines.append("")

    # Traffic
    traffic = report.get("traffic_access", {})
    if traffic:
        lines += [
            "## Traffic & Access", "",
            f"- **Est. Daily Traffic:** {traffic.get('estimated_daily_traffic', 'Unknown')}",
            f"- **Highway Proximity:** {traffic.get('highway_proximity', 'Unknown')}",
            f"- **Accessibility Score:** {traffic.get('accessibility_score', '?')}/10",
        ]
        for f in traffic.get("key_facts", []):
            lines.append(f"- {f}")
        lines.append("")

    # Competition
    comp = report.get("competition", {})
    if comp:
        lines += [
            "## Competition", "",
            f"- **Direct Competitors Nearby:** {comp.get('direct_competitors_nearby', 'Unknown')}",
            f"- **Market Saturation:** {comp.get('market_saturation', 'Unknown')}",
        ]
        for name in comp.get("competitor_names", []):
            lines.append(f"  - {name}")
        for f in comp.get("key_facts", []):
            lines.append(f"- {f}")
        lines.append("")

    # Gaming
    gaming = report.get("gaming_market", {})
    if gaming:
        lines += [
            "## Gaming Market", "",
            f"- **State Revenue Trend:** {gaming.get('state_gaming_revenue_trend', 'Unknown')}",
            f"- **Local Terminal Count:** {gaming.get('local_terminal_count', 'Unknown')}",
            f"- **Avg NTI/Terminal:** {gaming.get('avg_nti_per_terminal', 'Unknown')}",
            f"- **Regulatory Outlook:** {gaming.get('regulatory_outlook', 'Unknown')}",
        ]
        for f in gaming.get("key_facts", []):
            lines.append(f"- {f}")
        lines.append("")

    # Real Estate
    re = report.get("real_estate", {})
    if re:
        lines += [
            "## Real Estate Comps", "",
            f"- **Est. Cap Rate:** {re.get('estimated_cap_rate', 'Unknown')}",
            f"- **Valuation:** {re.get('valuation_assessment', 'Unknown')}",
        ]
        for c in re.get("comparable_sales", []):
            lines.append(f"  - {c}")
        for f in re.get("key_facts", []):
            lines.append(f"- {f}")
        lines.append("")

    # Regulatory
    reg = report.get("regulatory", {})
    if reg:
        lines += [
            "## Regulatory", "",
            f"- **Zoning:** {reg.get('zoning_status', 'Unknown')}",
        ]
        for r in reg.get("license_requirements", []):
            lines.append(f"  - {r}")
        lines.append("")

    # Economic
    econ = report.get("economic_outlook", {})
    if econ:
        lines += [
            "## Economic Outlook", "",
            f"- **Employment:** {econ.get('employment_trend', 'Unknown')}",
            f"- **Development Activity:** {econ.get('development_activity', 'Unknown')}",
        ]
        for f in econ.get("key_facts", []):
            lines.append(f"- {f}")
        lines.append("")

    # Risk Factors
    risks = report.get("risk_factors", [])
    if risks:
        lines += ["## Risk Factors", "",
                   "| Risk | Severity | Mitigation |",
                   "|------|----------|------------|"]
        for r in risks:
            lines.append(f"| {r.get('risk', '')} | {r.get('severity', '')} | {r.get('mitigation', '')} |")
        lines.append("")

    # Data Gaps
    gaps = report.get("data_gaps", [])
    if gaps:
        lines += ["## Data Gaps", ""]
        for g in gaps:
            lines.append(f"- ⚠️ {g}")
        lines.append("")

    # Recommendations
    recs = report.get("recommendations", [])
    if recs:
        lines += ["## Recommendations", ""]
        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. {r}")
        lines.append("")

    lines += [
        "---",
        f"*Generated by Arkain Market Research Engine | "
        f"Cost: ${meta.get('llm_cost_usd', 0):.4f}*",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _parse_location(address: str) -> tuple:
    """Best-effort parse city, state, county from address string."""
    parts = [p.strip() for p in address.split(",")]

    state = ""
    city = ""
    county = ""

    if len(parts) >= 3:
        city = parts[-2].strip()
        state_zip = parts[-1].strip().split()
        state = state_zip[0] if state_zip else ""
    elif len(parts) == 2:
        city = parts[0].strip()
        state_zip = parts[1].strip().split()
        state = state_zip[0] if state_zip else ""
    elif len(parts) == 1:
        # Try to extract state code from end
        words = address.split()
        for w in reversed(words):
            if len(w) == 2 and w.isalpha():
                state = w.upper()
                break
        city = " ".join(words[:3]) if len(words) > 3 else address

    return city, state.upper(), county
