"""
engine.strategic.stages — LLM-Powered Cognitive Functions
=============================================================
Production stages that use real LLM reasoning + engine tools.

Each stage:
  1. Builds a detailed system prompt defining role + output schema
  2. Assembles context from prior stages + raw scenario
  3. Defines available tools (mapped to real engine functions)
  4. Calls the LLM via llm_client with tool use enabled
  5. Parses structured JSON output
  6. Falls back to rule-based if LLM unavailable

When deployed with API keys, the LLM:
  - Calls egm_predict for real gaming revenue forecasts
  - Runs Monte Carlo simulations via simulate_contract
  - Pulls portfolio data via portfolio_dashboard
  - Computes amortization, IRR, DSCR via financial tools
  - Cross-references deal_impact for concentration analysis
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.strategic.llm_client import LLMClient, ToolDefinition

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# TOOL SCHEMAS (JSON Schema format for LLM tool use)
# ═══════════════════════════════════════════════════════════════

TOOL_SCHEMAS = {
    "egm_predict": {
        "name": "egm_predict",
        "description": (
            "Predict gaming terminal (EGM/VGT) performance for a location. "
            "Returns coin_in, hold_pct, net_win at p10/p50/p90 quantiles, "
            "confidence score, and similar locations. Use this to get real "
            "revenue projections for any gaming-related scenario."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "venue_type": {"type": "string", "description": "Type: bar, restaurant, gas_station, truck_stop, fraternal, other", "default": "bar"},
                "state": {"type": "string", "description": "US state code (e.g. IL, NV)", "default": "IL"},
                "terminal_count": {"type": "integer", "description": "Number of gaming terminals", "default": 5},
                "municipality": {"type": "string", "description": "City/municipality name"},
            },
            "required": ["venue_type", "state"],
        },
    },
    "simulate_contract": {
        "name": "simulate_contract",
        "description": (
            "Run Monte Carlo simulation on a gaming contract. "
            "Models revenue_share, flat_lease, or hybrid agreements "
            "with 5000 scenarios. Returns host cash flow quantiles, "
            "IRR distribution, downside risk, and guardrail analysis. "
            "Use for any scenario involving gaming operator agreements."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agreement_type": {"type": "string", "enum": ["revenue_share", "flat_lease", "hybrid"], "default": "revenue_share"},
                "operator_split": {"type": "number", "description": "Operator revenue share (0-1)", "default": 0.65},
                "host_split": {"type": "number", "description": "Host revenue share (0-1)", "default": 0.35},
                "acquisition_cost": {"type": "number", "description": "Total acquisition cost ($)"},
                "contract_months": {"type": "integer", "description": "Contract duration in months", "default": 60},
                "terminal_count": {"type": "integer", "default": 5},
                "coin_in_p10": {"type": "number"}, "coin_in_p50": {"type": "number"}, "coin_in_p90": {"type": "number"},
                "hold_pct_p10": {"type": "number"}, "hold_pct_p50": {"type": "number"}, "hold_pct_p90": {"type": "number"},
                "num_simulations": {"type": "integer", "default": 5000},
                "seed": {"type": "integer", "default": 42},
            },
            "required": ["agreement_type"],
        },
    },
    "evaluate_deal": {
        "name": "evaluate_deal",
        "description": (
            "Run the full 7-stage deal evaluation pipeline on a property. "
            "Returns scored recommendation (GO/HOLD/NO_GO) with financials, "
            "risk analysis, and gaming upside. Use for acquisition analysis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purchase_price": {"type": "number", "description": "Property purchase price ($)"},
                "noi": {"type": "number", "description": "Net operating income ($/yr)"},
                "property_type": {"type": "string", "default": "gas_station"},
                "address": {"type": "string"},
                "state": {"type": "string"},
                "deal_name": {"type": "string"},
                "terminal_count": {"type": "integer"},
            },
            "required": ["purchase_price"],
        },
    },
    "portfolio_dashboard": {
        "name": "portfolio_dashboard",
        "description": (
            "Get current portfolio snapshot: total value, debt, equity, "
            "state/type concentration, gaming exposure, debt maturity ladder. "
            "Use to understand existing portfolio context for any strategic decision."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "deal_impact": {
        "name": "deal_impact",
        "description": (
            "Analyze how a new deal would affect portfolio concentration "
            "(HHI by state, type, leverage). Use to check if a new "
            "acquisition improves or worsens diversification."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "state": {"type": "string"},
                "current_value": {"type": "number"},
                "property_type": {"type": "string"},
            },
            "required": ["name", "state", "current_value"],
        },
    },
    "amortize": {
        "name": "amortize",
        "description": "Compute loan amortization schedule. Returns payment breakdown, total interest, and payoff schedule.",
        "parameters": {
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "Loan amount ($)"},
                "annual_rate": {"type": "number", "description": "Annual interest rate (decimal, e.g. 0.07)"},
                "months": {"type": "integer", "description": "Loan term in months"},
            },
            "required": ["principal", "annual_rate", "months"],
        },
    },
    "dscr": {
        "name": "dscr",
        "description": "Compute Debt Service Coverage Ratio. Returns DSCR value and pass/fail assessment.",
        "parameters": {
            "type": "object",
            "properties": {
                "noi": {"type": "number", "description": "Annual net operating income ($)"},
                "annual_debt_service": {"type": "number", "description": "Annual debt payments ($)"},
            },
            "required": ["noi", "annual_debt_service"],
        },
    },
    "egm_market_health": {
        "name": "egm_market_health",
        "description": "Get EGM market health statistics for a state (location count, avg NTI, growth trends).",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "US state code"},
            },
            "required": ["state"],
        },
    },
    "web_search": {
        "name": "web_search",
        "description": (
            "Search the web for current information. Use for market data, "
            "demographics, competitor analysis, regulatory changes, news, "
            "property comps, traffic data, or any external data not in the "
            "internal database. Returns search results as text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keep short and specific, 2-6 words)"},
            },
            "required": ["query"],
        },
    },
    "market_research": {
        "name": "market_research",
        "description": (
            "Run comprehensive market research for a specific site/address. "
            "Executes 15-20 web searches across demographics, traffic, competition, "
            "gaming market, real estate comps, regulatory, economic indicators, and "
            "risk factors. Returns a structured research report. Use this for deep "
            "due diligence on a specific property or location."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Full property address"},
                "property_type": {"type": "string", "description": "gas_station, bar, restaurant, truck_stop, etc.", "default": "gas_station"},
                "context": {"type": "string", "description": "Additional context (e.g. 'evaluating for gaming expansion')"},
                "city": {"type": "string"}, "state": {"type": "string"}, "county": {"type": "string"},
                "purchase_price": {"type": "number"}, "noi": {"type": "number"},
                "terminal_count": {"type": "integer"},
            },
            "required": ["address"],
        },
    },
    "news_search": {
        "name": "news_search",
        "description": "Search recent news articles. Use for regulatory changes, market disruptions, industry news.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "News search query"},
            },
            "required": ["query"],
        },
    },
    "local_search": {
        "name": "local_search",
        "description": "Search for local businesses and places. Use for competition analysis, nearby amenities.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Local search query"},
                "location": {"type": "string", "description": "City, State for location bias"},
            },
            "required": ["query"],
        },
    },
    "construction_estimate": {
        "name": "construction_estimate",
        "description": (
            "Get a quick construction cost estimate for a property. Returns total cost, "
            "cost/sqft, schedule duration, manpower needs, and feasibility assessment. "
            "Use when evaluating whether renovation/construction costs make a deal viable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "property_type": {"type": "string", "description": "gas_station, bar, restaurant, retail, office", "default": "gas_station"},
                "project_type": {"type": "string", "description": "renovation, new_build, tenant_improvement", "default": "renovation"},
                "sqft": {"type": "number", "description": "Total square footage"},
                "terminal_count": {"type": "integer", "description": "Number of gaming terminals (0 if none)", "default": 0},
                "state": {"type": "string", "description": "State code for location factor", "default": "IL"},
                "city": {"type": "string", "description": "City for local cost adjustment"},
            },
            "required": ["property_type", "sqft"],
        },
    },
    "construction_feasibility": {
        "name": "construction_feasibility",
        "description": (
            "Full construction feasibility analysis. Provide scope details or document text. "
            "Returns detailed cost estimate with line items, schedule with critical path, "
            "manpower takeoff, and go/no-go recommendation. Use for deep construction due diligence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "object", "description": "Construction scope dict (optional)"},
                "document_text": {"type": "string", "description": "Text from construction documents (optional)"},
                "address": {"type": "string"}, "state": {"type": "string", "default": "IL"},
                "city": {"type": "string"}, "budget": {"type": "number", "default": 0},
                "acquisition_price": {"type": "number", "default": 0},
                "noi": {"type": "number", "default": 0},
            },
        },
    },
    "multi_search": {
        "name": "multi_search",
        "description": (
            "Search across multiple providers (Serper/Google/Anthropic) simultaneously "
            "for broader coverage. Returns deduplicated results ranked by relevance. "
            "Use for comprehensive research on a topic."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "default": 8},
                "search_type": {"type": "string", "enum": ["search", "news", "places"], "default": "search"},
                "location": {"type": "string", "description": "Location for local search bias"},
            },
            "required": ["query"],
        },
    },
    "find_similar_sites": {
        "name": "find_similar_sites",
        "description": (
            "Search institutional memory for previously researched sites similar to this one. "
            "Returns past research reports and scores for comparable properties."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Property address to find comps for"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["address"],
        },
    },
    "find_construction_comps": {
        "name": "find_construction_comps",
        "description": (
            "Search historical construction cost data for similar past projects. "
            "Returns cost/sqft benchmarks from comparable builds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_type": {"type": "string", "description": "renovation, new_build, tenant_improvement"},
                "location": {"type": "string", "description": "City/state for the project"},
            },
            "required": ["project_type"],
        },
    },
    "pull_comps": {
        "name": "pull_comps",
        "description": (
            "Pull comparable sales for a property. Searches vector store + web. "
            "Returns stored comps and web research findings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Subject property address"},
                "radius_miles": {"type": "number", "default": 3},
                "property_type": {"type": "string", "default": "gas_station"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["address"],
        },
    },
    "county_tax_lookup": {
        "name": "county_tax_lookup",
        "description": "Look up county tax/assessment records for a property via web search.",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string"}, "parcel_id": {"type": "string"},
                "county": {"type": "string"}, "state": {"type": "string"},
            },
            "required": ["address"],
        },
    },
    "analyze_lease": {
        "name": "analyze_lease",
        "description": (
            "Analyze lease document text for key clauses, risks, and financial terms. "
            "Returns detected clauses and LLM analysis if available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lease_text": {"type": "string", "description": "Raw lease text"},
                "lease_type": {"type": "string", "enum": ["commercial", "ground", "triple_net", "gross"]},
                "focus_areas": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["lease_text"],
        },
    },
    "generate_term_sheets": {
        "name": "generate_term_sheets",
        "description": (
            "Generate lender term sheet variants (SBA 504, SBA 7a, conventional, bridge, CMBS). "
            "Returns payment schedules, DSCR, and qualification requirements."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "property_type": {"type": "string"}, "purchase_price": {"type": "number"},
                "noi": {"type": "number"}, "borrower_experience": {"type": "string"},
                "credit_score": {"type": "integer"},
                "loan_types": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["purchase_price", "noi"],
        },
    },
    "eb5_job_impact": {
        "name": "eb5_job_impact",
        "description": (
            "Compute EB-5 visa job creation estimates using RIMS II multipliers. "
            "Returns jobs created, visas supportable, and capital potential."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "total_investment": {"type": "number"}, "construction_cost": {"type": "number"},
                "operating_revenue": {"type": "number"}, "state": {"type": "string"},
                "industry_naics": {"type": "string", "default": "447110"},
            },
            "required": ["total_investment"],
        },
    },
}


# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ═══════════════════════════════════════════════════════════════

COMPRESSION_PROMPT = """You are a Structuring Analyst in a strategic intelligence pipeline.

Your job: decompose a raw scenario into a structured, analyzable model. Extract every implicit assumption, identify all key decision variables, define scope boundaries, and flag information gaps.

You have access to tools that can pull real data:
- egm_predict / egm_market_health: Gaming terminal revenue predictions and market stats
- evaluate_deal: Run the full deal evaluation pipeline on a property
- amortize: Compute loan amortization schedules
- web_search: Search the web for demographics, comps, regulations, market data, news
- multi_search: Search across multiple providers (Serper/Google/Anthropic) for broader coverage
- market_research: Run a comprehensive 15-20 query market research report for a site
- construction_estimate: Quick construction cost estimate (scope, schedule, manpower)
- find_similar_sites: Search institutional memory for similar previously-analyzed properties

USE WEB SEARCH AND MULTI_SEARCH aggressively to gather real data about the location, market, competition, and regulatory environment. Don't rely on assumptions when you can look up facts.

If the scenario involves a specific address, call market_research to get a full site report.
If it mentions gaming/VGT/terminals, call egm_predict to get revenue projections.
If financing is mentioned, call amortize to model debt service.
If construction/renovation is mentioned, call construction_estimate to get cost/schedule data.
Call find_similar_sites to see if we've analyzed anything comparable before.

CRITICAL: You must respond with ONLY a JSON object (no markdown, no explanation) matching this exact schema:
{
  "structured_scenario": "<comprehensive structured summary with real numbers from tools and web search>",
  "explicit_assumptions": ["<assumption 1>", "<assumption 2>", ...],
  "scope_boundaries": ["<boundary 1>", ...],
  "key_variables": ["<variable 1>", ...],
  "missing_info": ["<gap 1>", ...],
  "tool_data": {"<tool_name>": <summary of key data pulled>, ...},
  "web_research": {"<topic>": "<key finding>", ...},
  "construction": {"estimated_cost": 0, "timeline_weeks": 0, "feasibility": "viable|risky|prohibitive"}
}

Be specific and quantitative. Embed real numbers from tools and searches into your analysis."""

DECISION_PREP_PROMPT = """You are a Decision Analyst in a strategic intelligence pipeline.

Your job: assess whether this scenario has enough information and manageable risk for a sound decision. You receive the compression stage output with structured data and any tool results.

Available tools:
- portfolio_dashboard: Get current portfolio state (assets, debt, concentration)
- deal_impact: Check how a new deal affects portfolio diversification
- web_search: Look up any external data (regulations, market trends, competitor info)

If the scenario involves portfolio implications, call portfolio_dashboard.
If a specific deal is mentioned, call deal_impact.
Use web_search to verify any assumptions or fill information gaps from the compression stage.

CRITICAL: Respond with ONLY a JSON object:
{
  "decision_criteria": ["<criterion 1>", ...],
  "missing_info": ["<gap 1>", ...],
  "gating_risks": ["<risk 1>", ...],
  "preliminary_decision": "GO" | "MODIFY" | "NO_GO",
  "triage_rationale": "<explanation with specific numbers and data>",
  "triage_score": 0.0-1.0,
  "info_completeness": 0.0-1.0,
  "portfolio_context": "<summary of portfolio data if retrieved, else 'not available'>"
}

Base your triage on REAL DATA from tools and web search, not assumptions."""

SCENARIOS_PROMPT = """You are a Scenario Analyst in a strategic intelligence pipeline.

Your job: generate base/bull/bear cases with REAL financial modeling. You have access to:
- egm_predict: Get gaming revenue quantiles (p10/p50/p90)
- simulate_contract: Run Monte Carlo simulation with those quantiles (5000 scenarios)
- amortize: Compute real debt service numbers
- dscr: Check debt service coverage ratio
- web_search / multi_search: Look up market comps, interest rates, recent transactions, benchmarks
- construction_estimate: Get construction cost/schedule estimates for each scenario
- find_construction_comps: Look up historical construction costs for similar past projects

CRITICAL INSTRUCTIONS:
- If gaming revenue is relevant, call egm_predict first, then simulate_contract with those quantiles
- If financing is involved, call amortize to get real debt service numbers
- If construction/renovation is part of the deal, call construction_estimate for each scenario
  with different quality_level/scope to show cost range across cases
- Use multi_search to find comparable transactions, current interest rates, market benchmarks
- Use find_construction_comps to benchmark against past projects
- Ground every number in real data — no made-up multipliers

Respond with ONLY a JSON object:
{
  "cases": [
    {
      "name": "base",
      "description": "<grounded in real tool data>",
      "probability": 0.0-1.0,
      "key_assumptions": ["..."],
      "expected_outcome": "<with specific dollar amounts from tools>",
      "financial_impact": {"<metric>": <value>, ...}
    },
    {"name": "bull", ...},
    {"name": "bear", ...}
  ],
  "sensitivities": ["<top sensitivity with quantified range>", ...],
  "second_order_effects": ["...", ...],
  "monte_carlo_summary": "<summary of simulation results if run, else 'not applicable'>"
}

Probabilities must sum to 1.0. Each case should reference specific numbers from tool outputs."""

PATTERNS_PROMPT = """You are a Pattern Analyst in a strategic intelligence pipeline.

Your job: identify failure modes, leverage points, contradictions, and analogous situations.

Available tools:
- portfolio_dashboard: Current portfolio exposure and concentration
- deal_impact: Check if new deal worsens diversification (HHI analysis)
- egm_market_health: Market-level gaming statistics
- web_search / multi_search: Search multiple providers for comprehensive coverage
- news_search: Recent news articles, regulatory changes, market disruptions
- local_search: Find nearby businesses, competitors, amenities
- construction_estimate: Check construction feasibility if relevant

Use web_search and multi_search to find:
- Recent regulatory changes that could affect this deal
- Competitor actions or market disruptions
- Similar deals that succeeded or failed (analogies)
- Risk factors specific to this location/market
- Construction cost trends if renovation is involved

Respond with ONLY a JSON object:
{
  "failure_modes": [
    {
      "domain": "execution|regulatory|market|financial|partner|operational",
      "description": "<specific to this scenario>",
      "probability": "high|medium|low",
      "severity": "critical|major|minor",
      "mitigation": "<actionable mitigation with specifics>",
      "data_support": "<what data/evidence supports this assessment>"
    }, ...
  ],
  "leverage_points": ["<specific high-impact action>", ...],
  "contradictions": ["<internal inconsistency found>", ...],
  "analogous_situations": ["<relevant precedent or pattern from web research>", ...],
  "portfolio_risks": "<how this interacts with existing portfolio, from tool data>"
}

Ground every finding in evidence from tools or web search."""

SYNTHESIS_PROMPT = """You are an Executive Synthesizer — the final stage of a strategic intelligence pipeline.

You receive ALL prior stage outputs: structured scenario, decision prep, scenario modeling (with real Monte Carlo data), and pattern analysis. Your job: produce the definitive decision.

DO NOT call any tools. Synthesize everything you've been given.

Respond with ONLY a JSON object:
{
  "decision": "GO" | "MODIFY" | "NO_GO",
  "confidence": 0.0-1.0,
  "swot": {
    "strengths": ["..."],
    "weaknesses": ["..."],
    "opportunities": ["..."],
    "threats": ["..."]
  },
  "recommendation": "<2-3 sentence executive recommendation with key numbers>",
  "key_findings": ["<finding 1>", ...],
  "next_actions": [
    {"action": "...", "owner": "...", "timeline": "...", "priority": "high|medium|low", "dependencies": []},
    ...
  ],
  "decision_rationale": "<detailed rationale citing specific data from prior stages>"
}

Your confidence should reflect:
- Quality and completeness of data gathered by prior stages
- Severity of failure modes identified
- Spread between bull and bear cases
- Number and severity of unresolved gaps

Be decisive. Executives need a clear recommendation, not hedging."""


# ═══════════════════════════════════════════════════════════════
# STAGE FUNCTIONS (LLM-first, rule-based fallback)
# ═══════════════════════════════════════════════════════════════

def stage_compression(
    inputs: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "cheap_structured",
) -> Dict:
    """Stage 1: Decompose scenario into structured form.

    LLM path: Reasons about scenario, may call egm_predict/evaluate_deal/amortize.
    Fallback: Rule-based keyword extraction.
    """
    scenario_text = inputs.get("scenario_text", "").strip()
    if not scenario_text:
        return {"status": "fail", "errors": ["scenario_text is required"]}

    if llm_client and tool_handlers:
        try:
            return _llm_compression(inputs, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM compression failed, falling back to rules: {e}")

    return _rule_compression(inputs)


def stage_decision_prep(
    inputs: Dict, compression: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "cheap_structured",
) -> Dict:
    """Stage 2: Assess decision readiness with portfolio context."""
    if llm_client and tool_handlers:
        try:
            return _llm_decision_prep(inputs, compression, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM decision_prep failed, falling back: {e}")

    return _rule_decision_prep(inputs, compression)


def stage_scenarios(
    inputs: Dict, compression: Dict, decision_prep: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "strategic_deep",
) -> Dict:
    """Stage 3: Generate scenarios with REAL Monte Carlo + EGM data."""
    if llm_client and tool_handlers:
        try:
            return _llm_scenarios(inputs, compression, decision_prep, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM scenarios failed, falling back: {e}")

    return _rule_scenarios(inputs, compression, decision_prep)


def stage_patterns(
    inputs: Dict, compression: Dict, decision_prep: Dict, scenarios: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "strategic_deep",
) -> Dict:
    """Stage 4: Failure modes + leverage with portfolio cross-reference."""
    if llm_client and tool_handlers:
        try:
            return _llm_patterns(inputs, compression, decision_prep, scenarios, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM patterns failed, falling back: {e}")

    return _rule_patterns(inputs, compression, decision_prep, scenarios)


def stage_synthesis(
    inputs: Dict, compression: Dict, decision_prep: Dict,
    scenarios: Dict, patterns: Dict, *,
    llm_client=None,
    route_tier: str = "strategic_deep",
) -> Dict:
    """Stage 5: Final synthesis — LLM only, no tools."""
    if llm_client:
        try:
            return _llm_synthesis(inputs, compression, decision_prep, scenarios, patterns, llm_client, route_tier)
        except Exception as e:
            logger.warning(f"LLM synthesis failed, falling back: {e}")

    return _rule_synthesis(inputs, compression, decision_prep, scenarios, patterns)


# ═══════════════════════════════════════════════════════════════
# LLM STAGE IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════

def _build_tools(tool_names: List[str], handlers: Dict) -> list:
    """Build ToolDefinition list from names and handler map."""
    from engine.strategic.llm_client import ToolDefinition
    tools = []
    for name in tool_names:
        schema = TOOL_SCHEMAS.get(name)
        handler = handlers.get(name)
        if schema and handler:
            tools.append(ToolDefinition(
                name=schema["name"],
                description=schema["description"],
                parameters=schema["parameters"],
                handler=handler,
            ))
    return tools


def _llm_compression(inputs: Dict, llm_client, handlers: Dict, route: str) -> Dict:
    from engine.strategic.llm_client import parse_json_from_llm

    user_msg = (
        f"SCENARIO TITLE: {inputs.get('title', 'Untitled')}\n\n"
        f"SCENARIO TEXT:\n{inputs.get('scenario_text', '')}\n\n"
        f"OBJECTIVES: {json.dumps(inputs.get('objectives', []))}\n"
        f"CONSTRAINTS: {json.dumps(inputs.get('constraints', []))}\n"
        f"BUDGET: ${inputs.get('budget_usd', 0):,.0f}\n"
        f"TIME HORIZON: {inputs.get('time_horizon', 'medium')}\n"
        f"RISK TOLERANCE: {inputs.get('risk_tolerance', 'moderate')}\n"
        f"ASSUMPTIONS: {json.dumps(inputs.get('assumptions', []))}"
    )

    # Compression can use data tools + web search to ground the analysis
    tools = _build_tools(
        ["egm_predict", "evaluate_deal", "amortize", "egm_market_health",
         "web_search", "market_research", "construction_estimate",
         "multi_search", "find_similar_sites"],
        handlers,
    )

    resp = llm_client.call(COMPRESSION_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)

    if not result.get("structured_scenario"):
        raise ValueError("LLM compression returned empty structured_scenario")

    result["status"] = "pass"
    result["errors"] = []
    result["title"] = inputs.get("title", "")
    result["objectives"] = inputs.get("objectives", [])
    result["constraints"] = inputs.get("constraints", [])
    result["budget_usd"] = inputs.get("budget_usd", 0)
    result["time_horizon"] = inputs.get("time_horizon", "medium")
    result["risk_tolerance"] = inputs.get("risk_tolerance", "moderate")
    result["_llm_response"] = {
        "model": resp.model, "tokens": resp.input_tokens + resp.output_tokens,
        "cost": resp.cost_usd, "tool_calls": len(resp.tool_calls),
    }
    return result


def _llm_decision_prep(inputs: Dict, compression: Dict, llm_client, handlers: Dict, route: str) -> Dict:
    from engine.strategic.llm_client import parse_json_from_llm

    user_msg = (
        f"COMPRESSION OUTPUT:\n{json.dumps(compression, indent=2, default=str)}\n\n"
        f"ORIGINAL SCENARIO:\n{inputs.get('scenario_text', '')}"
    )

    tools = _build_tools(
        ["portfolio_dashboard", "deal_impact", "web_search", "news_search"],
        handlers,
    )
    resp = llm_client.call(DECISION_PREP_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)

    if result.get("preliminary_decision") not in ("GO", "MODIFY", "NO_GO"):
        result["preliminary_decision"] = "MODIFY"

    result["status"] = "pass"
    result["errors"] = []
    result.setdefault("missing_info", compression.get("missing_info", []))
    result["_llm_response"] = {
        "model": resp.model, "tokens": resp.input_tokens + resp.output_tokens,
        "cost": resp.cost_usd, "tool_calls": len(resp.tool_calls),
    }
    return result


def _llm_scenarios(inputs: Dict, compression: Dict, decision_prep: Dict, llm_client, handlers: Dict, route: str) -> Dict:
    from engine.strategic.llm_client import parse_json_from_llm

    user_msg = (
        f"SCENARIO: {inputs.get('scenario_text', '')}\n\n"
        f"COMPRESSION:\n{json.dumps(compression, indent=2, default=str)}\n\n"
        f"DECISION PREP:\n{json.dumps(decision_prep, indent=2, default=str)}\n\n"
        f"RISK TOLERANCE: {inputs.get('risk_tolerance', 'moderate')}\n"
        f"BUDGET: ${inputs.get('budget_usd', 0):,.0f}\n\n"
        "Use the tools to get REAL numbers for your scenarios. "
        "Call egm_predict first if gaming is relevant, then simulate_contract "
        "with those quantiles. Call amortize if there's financing."
    )

    tools = _build_tools(
        ["egm_predict", "simulate_contract", "amortize", "dscr",
         "web_search", "multi_search", "construction_estimate",
         "find_construction_comps"],
        handlers,
    )

    resp = llm_client.call(SCENARIOS_PROMPT, user_msg, route_tier=route, tools=tools, temperature=0.5)
    result = parse_json_from_llm(resp.text)

    # Validate cases
    cases = result.get("cases", [])
    if len(cases) < 3:
        raise ValueError(f"LLM returned {len(cases)} cases, need at least 3")

    # Normalize probabilities
    total_p = sum(c.get("probability", 0) for c in cases)
    if total_p > 0 and abs(total_p - 1.0) > 0.05:
        for c in cases:
            c["probability"] = c.get("probability", 0) / total_p

    result["status"] = "pass"
    result["errors"] = []
    result["_llm_response"] = {
        "model": resp.model, "tokens": resp.input_tokens + resp.output_tokens,
        "cost": resp.cost_usd, "tool_calls": len(resp.tool_calls),
        "tool_data": [{"tool": tc["tool"], "success": tc.get("success")} for tc in resp.tool_calls],
    }
    return result


def _llm_patterns(inputs, compression, decision_prep, scenarios, llm_client, handlers, route):
    from engine.strategic.llm_client import parse_json_from_llm

    user_msg = (
        f"SCENARIO: {inputs.get('scenario_text', '')}\n\n"
        f"COMPRESSION:\n{json.dumps(compression, indent=2, default=str)}\n\n"
        f"DECISION PREP:\n{json.dumps(decision_prep, indent=2, default=str)}\n\n"
        f"SCENARIOS:\n{json.dumps(scenarios, indent=2, default=str)}\n\n"
        "Cross-reference with portfolio data and market health. "
        "Call portfolio_dashboard and deal_impact to check concentration risks."
    )

    tools = _build_tools(
        ["portfolio_dashboard", "deal_impact", "egm_market_health",
         "web_search", "multi_search", "news_search", "local_search",
         "construction_estimate"],
        handlers,
    )

    resp = llm_client.call(PATTERNS_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)

    if not result.get("failure_modes"):
        raise ValueError("LLM patterns returned no failure modes")

    result["status"] = "pass"
    result["errors"] = []
    result["_llm_response"] = {
        "model": resp.model, "tokens": resp.input_tokens + resp.output_tokens,
        "cost": resp.cost_usd, "tool_calls": len(resp.tool_calls),
    }
    return result


def _llm_synthesis(inputs, compression, decision_prep, scenarios, patterns, llm_client, route):
    from engine.strategic.llm_client import parse_json_from_llm

    user_msg = (
        f"TITLE: {inputs.get('title', 'Untitled')}\n\n"
        f"COMPRESSION:\n{json.dumps(compression, indent=2, default=str)}\n\n"
        f"DECISION PREP:\n{json.dumps(decision_prep, indent=2, default=str)}\n\n"
        f"SCENARIOS:\n{json.dumps(scenarios, indent=2, default=str)}\n\n"
        f"PATTERNS:\n{json.dumps(patterns, indent=2, default=str)}\n\n"
        "Synthesize all of the above into your final decision. "
        "Reference specific numbers from the tool data in prior stages. "
        "Be direct and decisive."
    )

    # Synthesis uses no tools — pure reasoning on all prior data
    resp = llm_client.call(SYNTHESIS_PROMPT, user_msg, route_tier=route, tools=None, temperature=0.3)
    result = parse_json_from_llm(resp.text)

    if result.get("decision") not in ("GO", "MODIFY", "NO_GO"):
        result["decision"] = "MODIFY"
    conf = result.get("confidence", 0.5)
    result["confidence"] = max(0.01, min(0.99, conf))

    result["status"] = "pass"
    result["errors"] = []
    result["_llm_response"] = {
        "model": resp.model, "tokens": resp.input_tokens + resp.output_tokens,
        "cost": resp.cost_usd,
    }
    return result


# ═══════════════════════════════════════════════════════════════
# RULE-BASED FALLBACKS (original pure functions)
# ═══════════════════════════════════════════════════════════════

def _rule_compression(inputs: Dict) -> Dict:
    """Fallback compression using keyword extraction."""
    text = inputs.get("scenario_text", "")
    title = inputs.get("title", "Untitled")
    objectives = inputs.get("objectives", [])
    constraints = inputs.get("constraints", [])
    assumptions = inputs.get("assumptions", [])
    budget = inputs.get("budget_usd", 0)
    horizon = inputs.get("time_horizon", "medium")
    risk = inputs.get("risk_tolerance", "moderate")

    key_vars = [f"Achievement of: {o[:80]}" for o in objectives[:5]]
    key_vars += [f"Constraint: {c[:80]}" for c in constraints[:3]]
    kws = ["revenue","cost","timeline","market","competition","regulation","demand","price","capacity","growth"]
    for kw in kws:
        if kw in text.lower() and len(key_vars) < 10:
            key_vars.append(f"Variable: {kw}")

    explicit = list(assumptions) or []
    if not explicit:
        if budget > 0: explicit.append(f"Budget of ${budget:,.0f} is available")
        explicit.append(f"Analysis based on {horizon}-term horizon")
        explicit.append("Current market conditions remain broadly stable")

    missing = []
    if not objectives: missing.append("Explicit objectives not stated")
    if not constraints: missing.append("Constraints not defined")
    if budget == 0: missing.append("Budget/capital requirement not specified")
    if not assumptions: missing.append("Working assumptions not articulated")
    if len(text) < 100: missing.append("Scenario description is very brief")

    structured = f"SCENARIO: {title}\nNARRATIVE: {text[:500]}\nOBJECTIVES: {'; '.join(objectives) if objectives else 'Not specified'}\nCONSTRAINTS: {'; '.join(constraints) if constraints else 'None'}\nBUDGET: ${budget:,.0f}\nHORIZON: {horizon}\nRISK: {risk}"

    return {
        "status": "pass", "errors": [], "structured_scenario": structured,
        "explicit_assumptions": explicit, "scope_boundaries": [f"Time horizon: {horizon}"],
        "key_variables": key_vars, "missing_info": missing, "title": title,
        "objectives": objectives, "constraints": constraints,
        "budget_usd": budget, "time_horizon": horizon, "risk_tolerance": risk,
    }


def _rule_decision_prep(inputs: Dict, compression: Dict) -> Dict:
    missing = compression.get("missing_info", [])
    objectives = compression.get("objectives", [])
    budget = compression.get("budget_usd", 0)

    criteria = [f"Does the plan achieve: {o}?" for o in objectives[:3]]
    if budget > 0: criteria.append(f"Total cost within ${budget:,.0f}?")
    criteria += ["Is timeline realistic?", "Are risks mitigable?"]
    if not criteria:
        criteria = ["Net positive value?", "Risks proportionate?", "Clear execution path?"]

    text = inputs.get("scenario_text", "").lower()
    risks = []
    for domain, signals in {"regulatory": ["regulation","compliance","permit"], "financial": ["funding","capital","debt","loan"], "market": ["competition","demand","market"], "execution": ["timeline","deadline","capacity"], "partner": ["partner","vendor","operator"]}.items():
        if any(s in text for s in signals):
            risks.append(f"{domain.title()} risk detected")
    for c in compression.get("constraints", [])[:3]:
        risks.append(f"Constraint: {c}")

    completeness = max(0, 1.0 - len(missing) * 0.15)
    clarity = min(1.0, len(objectives) * 0.25) if objectives else 0.3
    score = completeness * 0.4 + clarity * 0.3 + (1.0 - min(1.0, len(risks)*0.2)) * 0.3

    prelim = "GO" if score >= 0.6 else "MODIFY" if score >= 0.35 else "NO_GO"

    return {
        "status": "pass", "errors": [], "decision_criteria": criteria,
        "missing_info": missing, "gating_risks": risks[:8],
        "preliminary_decision": prelim, "triage_rationale": f"Score: {score:.2f}",
        "triage_score": round(score, 3), "info_completeness": round(completeness, 3),
    }


def _rule_scenarios(inputs: Dict, compression: Dict, decision_prep: Dict) -> Dict:
    risk = compression.get("risk_tolerance", "moderate")
    probs = {"conservative": (0.50,0.15,0.35), "moderate": (0.50,0.25,0.25), "aggressive": (0.50,0.35,0.15)}
    pb, pu, pr = probs.get(risk, probs["moderate"])
    budget = compression.get("budget_usd", 0)

    cases = [
        {"name": "base", "description": "Most likely outcome", "probability": pb,
         "key_assumptions": ["Current conditions persist"], "expected_outcome": "Objectives achieved within parameters",
         "financial_impact": {"roi_multiple": "1.0x", "capital_deployed": budget}},
        {"name": "bull", "description": "Upside scenario", "probability": pu,
         "key_assumptions": ["Favorable conditions"], "expected_outcome": "Objectives exceeded",
         "financial_impact": {"roi_multiple": "1.5x", "capital_deployed": budget}},
        {"name": "bear", "description": "Downside scenario", "probability": pr,
         "key_assumptions": ["Adverse conditions"], "expected_outcome": "Risk of capital impairment",
         "financial_impact": {"roi_multiple": "0.6x", "capital_deployed": budget}},
    ]

    return {
        "status": "pass", "errors": [], "cases": cases,
        "sensitivities": ["Revenue assumptions","Cost escalation","Timeline","Regulatory","Competition"],
        "second_order_effects": ["Competitor response unknown","Reputation effects","Resource allocation impact"],
    }


def _rule_patterns(inputs: Dict, compression: Dict, decision_prep: Dict, scenarios: Dict) -> Dict:
    text = inputs.get("scenario_text", "").lower()
    risks = decision_prep.get("gating_risks", [])
    modes = []
    for domain, signals, desc, sev in [
        ("execution", ["build","implement","launch"], "Execution risk", "major"),
        ("regulatory", ["regulation","permit","compliance","license"], "Regulatory risk", "critical"),
        ("market", ["market","demand","competition"], "Market risk", "major"),
        ("financial", ["cost","budget","funding","capital"], "Financial risk", "major"),
        ("partner", ["partner","vendor","operator"], "Partner risk", "major"),
    ]:
        if any(s in text for s in signals):
            modes.append({"domain": domain, "description": desc, "probability": "medium",
                         "severity": sev, "mitigation": f"Develop {domain} contingency plan"})
    if not modes:
        modes.append({"domain": "execution", "description": "General execution risk",
                      "probability": "medium", "severity": "major", "mitigation": "Clear ownership and milestones"})

    objectives = compression.get("objectives", [])
    leverage = [
        f"Focus on: {objectives[0][:60]}" if objectives else "Define primary objective",
        "Secure early wins", "De-risk before committing capital", "Build optionality",
    ]

    return {
        "status": "pass", "errors": [], "failure_modes": modes, "leverage_points": leverage,
        "contradictions": [], "analogous_situations": [],
    }


def _rule_synthesis(inputs, compression, decision_prep, scenarios, patterns):
    prelim = decision_prep.get("preliminary_decision", "MODIFY")
    score = decision_prep.get("triage_score", 0.5)
    modes = patterns.get("failure_modes", [])
    leverage = patterns.get("leverage_points", [])
    missing = compression.get("missing_info", [])
    risk = compression.get("risk_tolerance", "moderate")

    conf = max(0.05, min(0.99, score - len(modes)*0.04 - len(missing)*0.05 + len(leverage)*0.03))
    critical = sum(1 for m in modes if isinstance(m, dict) and m.get("severity") == "critical")

    if critical >= 2 or conf < 0.25: decision = "NO_GO"
    elif conf >= 0.6 and critical == 0: decision = "GO"
    else: decision = "MODIFY"

    if risk == "aggressive" and decision == "MODIFY" and conf >= 0.5: decision = "GO"
    elif risk == "conservative" and decision == "GO" and conf < 0.7: decision = "MODIFY"

    cases = scenarios.get("cases", [])
    objectives = compression.get("objectives", [])
    swot = {
        "strengths": [f"Clear objective: {objectives[0][:60]}" if objectives else "Analysis completed", leverage[0] if leverage else ""],
        "weaknesses": [c for c in patterns.get("contradictions", [])[:2]],
        "opportunities": [f"Bull case upside" if cases else ""],
        "threats": [f"{m.get('domain','')}: {m.get('description','')[:60]}" for m in modes[:3] if isinstance(m, dict)],
    }
    swot = {k: [v for v in vs if v] for k, vs in swot.items()}

    actions = []
    for m in missing[:2]:
        actions.append({"action": f"Resolve: {m}", "owner": "Analyst", "timeline": "1 week", "priority": "high", "dependencies": []})
    if decision != "NO_GO":
        actions.append({"action": "Draft execution plan", "owner": "Project lead", "timeline": "1 week", "priority": "high", "dependencies": []})

    return {
        "status": "pass", "errors": [], "decision": decision, "confidence": round(conf, 3),
        "swot": swot, "recommendation": f"Decision: {decision} (conf: {conf:.0%})",
        "key_findings": [f"{len(modes)} failure modes", f"{len(leverage)} leverage points"],
        "next_actions": actions, "decision_rationale": f"Triage score: {score}, {len(modes)} risks, {len(missing)} gaps",
    }


# ═══════════════════════════════════════════════════════════════
# EXTENSION STAGES (Phase 12)
# ═══════════════════════════════════════════════════════════════
# Optional stages that can be inserted into the pipeline.
# Each follows the same pattern: LLM path + rule fallback.

# ── Stage Prompts ─────────────────────────────────────────────

DATA_GATHERING_PROMPT = """You are a Data Gathering Analyst in a strategic intelligence pipeline.

Your job: Before any analysis begins, exhaustively gather all available data about the subject.

You have search tools — USE THEM ALL:
- multi_search: Search across multiple providers for broad coverage
- news_search: Find recent news, regulatory changes, market events
- local_search: Find nearby businesses, competitors, amenities
- market_research: Run comprehensive 17-query site research report
- find_similar_sites: Check if we've analyzed similar properties before
- pull_comps: Find comparable sales in the area
- county_tax_lookup: Get tax assessment and property tax data
- egm_market_health: Get gaming market statistics

Execute at minimum:
1. Full market research report for the address
2. Comparable sales search
3. County tax record lookup
4. News search for the area/market
5. Similar sites from institutional memory
6. Gaming market health check (if gaming property)

CRITICAL: Respond with ONLY a JSON object:
{
  "data_completeness_score": <0-1>,
  "sources_consulted": <count>,
  "market_research": {<summary of market research findings>},
  "comparable_sales": [<list of comps found>],
  "tax_assessment": {<tax data found>},
  "recent_news": [<relevant news items>],
  "prior_research": [<similar sites from memory>],
  "gaming_market": {<gaming stats if applicable>},
  "data_gaps": ["<gap 1>", "<gap 2>"],
  "key_findings": ["<finding 1>", "<finding 2>"]
}"""


COUNTERPARTY_RISK_PROMPT = """You are a Counterparty Risk Analyst in a strategic intelligence pipeline.

Your job: Assess the financial health and reliability of all counterparties in the deal.

Counterparties to evaluate:
- Seller / current owner
- Operator (if different from buyer)
- Tenants
- Lender(s)
- Gaming terminal operator/route operator
- General contractor
- Key suppliers

For each counterparty, assess:
1. Financial health indicators
2. Litigation history
3. Regulatory standing
4. Reputation and track record
5. Dependency risk (how critical are they? Can they be replaced?)

Use web_search and news_search to find:
- Lawsuits, liens, bankruptcies
- Regulatory violations or sanctions
- Business ratings and reviews
- Financial disclosures if public

CRITICAL: Respond with ONLY a JSON object:
{
  "counterparties": [
    {
      "name": "<name>",
      "role": "<role in deal>",
      "risk_rating": "LOW|MEDIUM|HIGH|CRITICAL",
      "financial_health": "<assessment>",
      "litigation_flags": ["<flag>"],
      "regulatory_status": "<clean|warning|violation>",
      "replacement_difficulty": "easy|moderate|difficult|impossible",
      "key_concerns": ["<concern>"],
      "mitigations": ["<mitigation>"]
    }
  ],
  "overall_counterparty_risk": "LOW|MEDIUM|HIGH",
  "deal_breakers": ["<if any>"],
  "recommended_protections": ["<protection>"]
}"""


LEGAL_RISK_PROMPT = """You are a Legal Risk Analyst in a strategic intelligence pipeline.

Your job: Identify all legal and regulatory risks that could affect the deal.

Risk categories:
1. ZONING & LAND USE — Is the intended use permitted? Variance needed?
2. ENVIRONMENTAL — Phase I/II concerns, UST, contamination history
3. GAMING REGULATION — License requirements, distance restrictions, moratoriums
4. TITLE & ENCUMBRANCES — Liens, easements, deed restrictions
5. LEASE RISKS — Assignment provisions, termination triggers, exclusivity
6. ADA COMPLIANCE — Accessibility requirements and exposure
7. BUILDING CODE — Certificate of occupancy, code violations, permits
8. EMPLOYMENT — Wage/hour, gaming employee licensing
9. TAX — Property tax appeals, assessment challenges, sales tax nexus
10. INSURANCE — Coverage gaps, environmental liability, liquor liability

Use web_search to research:
- Local zoning ordinances for the address
- State gaming regulations and recent changes
- Environmental records and violations
- Building permit history

CRITICAL: Respond with ONLY a JSON object:
{
  "legal_risks": [
    {
      "category": "<category>",
      "risk": "<description>",
      "severity": "LOW|MEDIUM|HIGH|CRITICAL",
      "probability": "LOW|MEDIUM|HIGH",
      "financial_exposure": "<$amount or range>",
      "regulatory_reference": "<citation if found>",
      "mitigation": "<recommended action>",
      "timeline": "<when this needs to be addressed>"
    }
  ],
  "overall_legal_risk": "LOW|MEDIUM|HIGH",
  "required_due_diligence": ["<item>"],
  "deal_conditions": ["<condition that must be met>"],
  "estimated_legal_costs": "<range>"
}"""


CAPITAL_STACK_PROMPT = """You are a Capital Stack Optimizer in a strategic intelligence pipeline.

Your job: Design the optimal capital structure for this deal.

Consider ALL capital sources:
1. Senior debt (conventional, SBA 504, SBA 7(a), CMBS, credit union)
2. Mezzanine debt
3. Seller financing / VTB (vendor take-back)
4. EB-5 capital (if qualifying project)
5. Equity (sponsor cash, JV partner, fund)
6. Tax credits / incentives (opportunity zones, historic, energy)
7. Gaming equipment financing (terminal operator financing)
8. TIF / economic development incentives

For each structure variant:
- Total sources and uses
- Blended cost of capital
- DSCR at each debt level
- Equity requirement and cash-on-cash return
- IRR sensitivity to key variables

Use tools:
- generate_term_sheets: Get specific loan terms for each debt layer
- amortize: Model each debt tranche
- dscr: Verify coverage ratios
- eb5_job_impact: Check EB-5 eligibility and job creation
- construction_estimate: Size construction financing if needed

CRITICAL: Respond with ONLY a JSON object:
{
  "recommended_structure": {
    "total_project_cost": <number>,
    "sources": [
      {"name": "<source>", "amount": <number>, "pct": <0-1>, "cost": "<rate%>", "terms": "<summary>"}
    ],
    "uses": [
      {"name": "<use>", "amount": <number>}
    ],
    "blended_cost_of_capital": <pct>,
    "equity_required": <number>,
    "projected_dscr": <number>,
    "projected_cash_on_cash": <pct>,
    "projected_irr": <pct>
  },
  "alternative_structures": [<same format>],
  "sensitivity_analysis": {
    "rate_increase_100bps": {"dscr": <n>, "cash_on_cash": <n>},
    "noi_decrease_10pct": {"dscr": <n>, "cash_on_cash": <n>},
    "cap_rate_expansion_50bps": {"equity_value_change": <n>}
  },
  "tax_incentives_available": ["<incentive>"],
  "recommendations": ["<rec>"]
}"""


# ── Stage Functions ───────────────────────────────────────────

def stage_data_gathering(
    inputs: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "cheap_structured",
) -> Dict:
    """Extension Stage: Exhaustive data gathering before analysis."""
    scenario_text = inputs.get("scenario_text", "")
    if llm_client and tool_handlers:
        try:
            return _llm_data_gathering(inputs, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM data_gathering failed: {e}")
    return _rule_data_gathering(inputs)


def stage_counterparty_risk(
    inputs: Dict, compression: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "strategic_fast",
) -> Dict:
    """Extension Stage: Counterparty risk assessment."""
    if llm_client and tool_handlers:
        try:
            return _llm_counterparty_risk(inputs, compression, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM counterparty_risk failed: {e}")
    return {"counterparties": [], "overall_counterparty_risk": "UNKNOWN", "status": "pass"}


def stage_legal_risk(
    inputs: Dict, compression: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "strategic_fast",
) -> Dict:
    """Extension Stage: Legal and regulatory risk assessment."""
    if llm_client and tool_handlers:
        try:
            return _llm_legal_risk(inputs, compression, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM legal_risk failed: {e}")
    return {"legal_risks": [], "overall_legal_risk": "UNKNOWN", "status": "pass"}


def stage_capital_stack(
    inputs: Dict, compression: Dict, scenarios: Dict, *,
    llm_client=None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    route_tier: str = "strategic_deep",
) -> Dict:
    """Extension Stage: Capital stack optimization."""
    if llm_client and tool_handlers:
        try:
            return _llm_capital_stack(inputs, compression, scenarios, llm_client, tool_handlers, route_tier)
        except Exception as e:
            logger.warning(f"LLM capital_stack failed: {e}")
    return _rule_capital_stack(inputs, compression, scenarios)


# ── LLM Implementations ──────────────────────────────────────

def _llm_data_gathering(inputs, llm_client, handlers, route):
    from engine.strategic.llm_client import parse_json_from_llm
    user_msg = f"Gather all available data for:\n{inputs.get('scenario_text', '')}"
    tools = _build_tools(
        ["multi_search", "market_research", "news_search", "local_search",
         "pull_comps", "county_tax_lookup", "find_similar_sites",
         "egm_market_health"],
        handlers,
    )
    resp = llm_client.call(DATA_GATHERING_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)
    result["status"] = "pass"
    result["tool_calls"] = len(resp.tool_calls)
    return result


def _rule_data_gathering(inputs):
    return {
        "status": "pass",
        "data_completeness_score": 0.1,
        "sources_consulted": 0,
        "data_gaps": ["No LLM available — manual data gathering required"],
        "key_findings": [],
    }


def _llm_counterparty_risk(inputs, compression, llm_client, handlers, route):
    from engine.strategic.llm_client import parse_json_from_llm
    import json
    user_msg = (
        f"Scenario:\n{inputs.get('scenario_text', '')}\n\n"
        f"Compression:\n{json.dumps(compression, indent=2, default=str)[:3000]}"
    )
    tools = _build_tools(
        ["web_search", "multi_search", "news_search"],
        handlers,
    )
    resp = llm_client.call(COUNTERPARTY_RISK_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)
    result["status"] = "pass"
    return result


def _llm_legal_risk(inputs, compression, llm_client, handlers, route):
    from engine.strategic.llm_client import parse_json_from_llm
    import json
    user_msg = (
        f"Scenario:\n{inputs.get('scenario_text', '')}\n\n"
        f"Compression:\n{json.dumps(compression, indent=2, default=str)[:3000]}"
    )
    tools = _build_tools(
        ["web_search", "multi_search", "county_tax_lookup", "analyze_lease"],
        handlers,
    )
    resp = llm_client.call(LEGAL_RISK_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)
    result["status"] = "pass"
    return result


def _llm_capital_stack(inputs, compression, scenarios, llm_client, handlers, route):
    from engine.strategic.llm_client import parse_json_from_llm
    import json
    user_msg = (
        f"Scenario:\n{inputs.get('scenario_text', '')}\n\n"
        f"Compression:\n{json.dumps(compression, indent=2, default=str)[:3000]}\n\n"
        f"Scenarios:\n{json.dumps(scenarios, indent=2, default=str)[:3000]}"
    )
    tools = _build_tools(
        ["generate_term_sheets", "amortize", "dscr", "eb5_job_impact",
         "construction_estimate", "web_search"],
        handlers,
    )
    resp = llm_client.call(CAPITAL_STACK_PROMPT, user_msg, route_tier=route, tools=tools)
    result = parse_json_from_llm(resp.text)
    result["status"] = "pass"
    return result


def _rule_capital_stack(inputs, compression, scenarios):
    """Rule-based capital stack when no LLM available."""
    pp = 0
    noi = 0
    # Try to extract from compression
    if isinstance(compression, dict):
        kv = compression.get("key_variables", [])
        for v in kv:
            if "price" in str(v).lower():
                import re
                nums = re.findall(r'[\d,]+', str(v))
                if nums:
                    try: pp = float(nums[0].replace(',', ''))
                    except: pass
            if "noi" in str(v).lower():
                import re
                nums = re.findall(r'[\d,]+', str(v))
                if nums:
                    try: noi = float(nums[0].replace(',', ''))
                    except: pass

    if pp <= 0:
        pp = 1_000_000  # default

    return {
        "status": "pass",
        "recommended_structure": {
            "total_project_cost": pp,
            "sources": [
                {"name": "Senior Debt (75% LTV)", "amount": pp * 0.75, "pct": 0.75, "cost": "7.25%"},
                {"name": "Equity", "amount": pp * 0.25, "pct": 0.25, "cost": "15-20% target IRR"},
            ],
            "equity_required": pp * 0.25,
        },
        "alternative_structures": [],
        "recommendations": ["Run with LLM for detailed multi-tranche analysis"],
    }
