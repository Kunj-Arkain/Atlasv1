"""
tests/test_strategic.py — Strategic Intelligence Layer Tests
================================================================
Tests all 7 phases including production LLM wiring.
Runs in rule-based fallback mode (no API keys in test env).
"""

import os
import json
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure fallback mode
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from engine.strategic.schema import (
    ScenarioInput, StrategicAnalysisResult, SWOTAnalysis, Decision,
)
from engine.strategic.stages import (
    stage_compression, stage_decision_prep, stage_scenarios,
    stage_patterns, stage_synthesis, TOOL_SCHEMAS,
)
from engine.strategic.pipeline import (
    StrategicPipeline, DEFAULT_STAGE_ROUTES, build_tool_handlers,
    STAGE_VALIDATORS, _validate_compression, _validate_decision_prep,
    _validate_scenarios, _validate_patterns, _validate_synthesis,
)
from engine.strategic.templates import default_scenario_templates, get_template
from engine.strategic.export import (
    export_memo_markdown, export_actions_csv, export_summary_json,
)
from engine.strategic.llm_client import (
    LLMClient, LLMResponse, ToolDefinition, ROUTE_MODELS,
    TOKEN_COSTS, parse_json_from_llm,
)


FULL = {
    "title": "Acquire Springfield Gas Station for Gaming Expansion",
    "scenario_text": (
        "Evaluate gas station acquisition at 456 Oak Ave, Springfield, IL "
        "for $850,000. 6 VGT terminal slots, high-traffic corridor. "
        "NOI $72,000/yr. Revenue share with J&J Ventures. "
        "Market has 4 competing locations. Budget $1.2M including renovation."
    ),
    "objectives": ["Achieve 15% IRR", "Generate $20K+/mo NTI", "Maintain diversification"],
    "constraints": ["Max price $900K", "75% LTV required", "IL gaming approval"],
    "time_horizon": "medium",
    "budget_usd": 1200000,
    "risk_tolerance": "moderate",
    "assumptions": ["Gaming market stable", "Rates below 8%", "No zoning changes"],
}

MINIMAL = {"title": "Quick", "scenario_text": "Should we expand into Nevada?"}


# ═══════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════

class TestSchema:
    def test_valid(self):
        si = ScenarioInput(title="T", scenario_text="Test scenario")
        assert si.validate() == []

    def test_requires_text(self):
        si = ScenarioInput(title="T", scenario_text="")
        assert len(si.validate()) > 0

    def test_decision_enum(self):
        assert Decision.GO.value == "GO"


# ═══════════════════════════════════════════════════════════════
# STAGES (rule-based fallback)
# ═══════════════════════════════════════════════════════════════

class TestStages:
    def test_compression(self):
        r = stage_compression(FULL)
        assert r["status"] == "pass"
        assert len(r["key_variables"]) >= 3
        assert r["budget_usd"] == 1200000

    def test_decision_prep(self):
        c = stage_compression(FULL)
        r = stage_decision_prep(FULL, c)
        assert r["preliminary_decision"] in ("GO", "MODIFY", "NO_GO")
        assert len(r["decision_criteria"]) >= 3

    def test_scenarios(self):
        c = stage_compression(FULL)
        dp = stage_decision_prep(FULL, c)
        r = stage_scenarios(FULL, c, dp)
        assert len(r["cases"]) == 3
        assert abs(sum(x["probability"] for x in r["cases"]) - 1.0) < 0.01

    def test_patterns(self):
        c = stage_compression(FULL)
        dp = stage_decision_prep(FULL, c)
        sc = stage_scenarios(FULL, c, dp)
        r = stage_patterns(FULL, c, dp, sc)
        assert len(r["failure_modes"]) >= 1
        assert len(r["leverage_points"]) >= 2

    def test_synthesis(self):
        c = stage_compression(FULL)
        dp = stage_decision_prep(FULL, c)
        sc = stage_scenarios(FULL, c, dp)
        pa = stage_patterns(FULL, c, dp, sc)
        r = stage_synthesis(FULL, c, dp, sc, pa)
        assert r["decision"] in ("GO", "MODIFY", "NO_GO")
        assert 0 < r["confidence"] < 1

    def test_empty_fails(self):
        r = stage_compression({"scenario_text": ""})
        assert r["status"] == "fail"


# ═══════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════

class TestPipeline:
    def test_full(self):
        p = StrategicPipeline()
        r = p.analyze(FULL)
        assert r["status"] == "completed"
        assert r["decision"] in ("GO", "MODIFY", "NO_GO")
        assert 0 < r["confidence"] < 1
        assert len(r["scenarios"]) == 3
        assert len(r["failure_modes"]) >= 1
        assert r["llm_active"] == False
        assert "llm_costs_by_stage" in r

    def test_stage_routes(self):
        p = StrategicPipeline()
        r = p.analyze(FULL)
        assert r["stage_routes"]["compression"] == "cheap_structured"
        assert r["stage_routes"]["synthesis"] == "strategic_deep"

    def test_custom_routes(self):
        custom = {s: "ultra" for s in DEFAULT_STAGE_ROUTES}
        p = StrategicPipeline(stage_routes=custom)
        r = p.analyze(FULL)
        assert r["stage_routes"]["synthesis"] == "ultra"

    def test_sub_tools(self):
        p = StrategicPipeline()
        assert any(p.swot_only(FULL).get(k) for k in ("strengths", "weaknesses"))
        assert len(p.stress_test(FULL)["failure_modes"]) >= 1
        assert len(p.scenario_simulate(FULL)["scenarios"]) == 3
        assert len(p.assumption_audit(FULL)["explicit_assumptions"]) >= 3

    def test_empty_no_go(self):
        p = StrategicPipeline()
        r = p.analyze({"title": "Empty", "scenario_text": ""})
        assert r["decision"] == "NO_GO"


# ═══════════════════════════════════════════════════════════════
# OODA VALIDATORS
# ═══════════════════════════════════════════════════════════════

class TestValidators:
    def test_compression_valid(self):
        assert _validate_compression(stage_compression(FULL)) == []

    def test_synthesis_bad_decision(self):
        issues = _validate_synthesis({"decision": "MAYBE", "confidence": 0.5, "swot": {"strengths": ["a"]}, "next_actions": ["x"]})
        assert any("decision" in i for i in issues)


# ═══════════════════════════════════════════════════════════════
# TOOL HANDLERS (real engine subsystems)
# ═══════════════════════════════════════════════════════════════

class TestToolHandlers:
    def test_build_all(self):
        h = build_tool_handlers()
        assert len(h) >= 17
        assert all(k in h for k in (
            "egm_predict", "simulate_contract", "portfolio_dashboard",
            "amortize", "dscr", "web_search", "market_research",
            "multi_search", "news_search", "local_search",
            "construction_estimate", "construction_feasibility",
            "find_similar_sites", "find_construction_comps",
        ))

    def test_monte_carlo(self):
        h = build_tool_handlers()
        r = h["simulate_contract"]({
            "agreement_type": "revenue_share", "operator_split": 0.65,
            "host_split": 0.35, "terminal_count": 6, "acquisition_cost": 850000,
            "contract_months": 60, "coin_in_p50": 80000, "coin_in_p10": 50000,
            "coin_in_p90": 120000, "hold_pct_p10": 0.22, "hold_pct_p50": 0.26,
            "hold_pct_p90": 0.31, "num_simulations": 500, "seed": 42,
        })
        assert r["num_simulations"] == 500
        assert "irr_p50" in r
        assert "net_win_p50" in r

    def test_amortize(self):
        h = build_tool_handlers()
        r = h["amortize"]({"principal": 637500, "annual_rate": 0.075, "months": 240})
        assert r["monthly_payment"] > 0

    def test_dscr(self):
        h = build_tool_handlers()
        r = h["dscr"]({"noi": 72000, "annual_debt_service": 60000})
        assert r["dscr"] == 1.2


# ═══════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════

class TestLLMClient:
    def test_route_models(self):
        assert len(ROUTE_MODELS) == 3
        assert "strategic_deep" in ROUTE_MODELS
        assert "cheap_structured" in ROUTE_MODELS

    def test_token_costs(self):
        assert len(TOKEN_COSTS) >= 5

    def test_json_parser(self):
        assert parse_json_from_llm('{"a": 1}') == {"a": 1}
        assert parse_json_from_llm('```json\n{"b": 2}\n```') == {"b": 2}
        assert parse_json_from_llm('Result: {"c": 3} done') == {"c": 3}
        assert parse_json_from_llm("no json here") == {}

    def test_tool_definition(self):
        td = ToolDefinition(name="t", description="d", parameters={}, handler=lambda x: x)
        assert td.name == "t"

    def test_tool_schemas(self):
        assert len(TOOL_SCHEMAS) >= 17
        for name in ("egm_predict", "simulate_contract", "amortize", "dscr",
                      "portfolio_dashboard", "deal_impact", "evaluate_deal",
                      "egm_market_health", "web_search", "market_research",
                      "multi_search", "news_search", "local_search",
                      "construction_estimate", "construction_feasibility",
                      "find_similar_sites", "find_construction_comps"):
            assert name in TOOL_SCHEMAS
            assert "parameters" in TOOL_SCHEMAS[name]


# ═══════════════════════════════════════════════════════════════
# LLM ACTIVATION
# ═══════════════════════════════════════════════════════════════

class TestLLMActivation:
    def test_no_key_fallback(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        p = StrategicPipeline()
        assert not p.is_llm_active

    def test_with_key_activates(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-fake"
        try:
            p = StrategicPipeline()
            assert p.is_llm_active
            assert len(p._tool_handlers) >= 17
        finally:
            os.environ.pop("ANTHROPIC_API_KEY")


# ═══════════════════════════════════════════════════════════════
# TEMPLATES + EXPORT + CONTRACTS
# ═══════════════════════════════════════════════════════════════

class TestTemplates:
    def test_count(self):
        assert len(default_scenario_templates()) == 5

    def test_lookup(self):
        assert get_template("gaming")["template_type"] == "gaming"
        assert get_template("nope")["template_type"] == "general"

    def test_routes(self):
        for t in default_scenario_templates():
            assert "stage_routes" in t
            assert "synthesis" in t["stage_routes"]


class TestExport:
    def test_memo(self):
        p = StrategicPipeline()
        r = p.analyze(FULL)
        md = export_memo_markdown(r)
        assert "SWOT" in md and "Pipeline Routing" in md

    def test_csv(self):
        p = StrategicPipeline()
        r = p.analyze(FULL)
        csv = export_actions_csv(r)
        assert csv.startswith("Priority")

    def test_json(self):
        p = StrategicPipeline()
        r = p.analyze(FULL)
        s = json.loads(export_summary_json(r))
        assert s["decision"] in ("GO", "MODIFY", "NO_GO")


class TestContracts:
    def test_register(self):
        from engine.contracts.validation import ContractRegistry
        from engine.contracts.strategic_contracts import register_strategic_contracts
        reg = ContractRegistry()
        register_strategic_contracts(reg)
        for s in ("intake", "compression", "decision_prep", "scenarios", "patterns", "synthesis"):
            assert reg.get(f"strategic.{s}") is not None


# ═══════════════════════════════════════════════════════════════
# MARKET RESEARCH MODULE
# ═══════════════════════════════════════════════════════════════

class TestResearch:
    def test_import(self):
        from engine.strategic.research import (
            MarketResearcher, format_research_markdown, _parse_location,
            _build_search_queries,
        )

    def test_parse_location(self):
        from engine.strategic.research import _parse_location
        city, state, county = _parse_location("456 Oak Ave, Springfield, IL 62701")
        assert city == "Springfield"
        assert state == "IL"

    def test_parse_location_short(self):
        from engine.strategic.research import _parse_location
        city, state, county = _parse_location("Springfield, IL")
        assert state == "IL"

    def test_query_generation(self):
        from engine.strategic.research import _build_search_queries
        queries = _build_search_queries(
            "456 Oak Ave", "Springfield", "IL", "Sangamon",
            "gas_station", "gaming terminal placement",
        )
        domains = set(q["domain"] for q in queries)
        # Should cover all research domains
        assert "demographics" in domains
        assert "traffic" in domains
        assert "competition" in domains
        assert "gaming" in domains  # context mentions gaming
        assert "real_estate" in domains
        assert "regulatory" in domains
        assert "economic" in domains
        assert "risk" in domains
        assert len(queries) >= 15  # Gaming scenario = more queries

    def test_query_generation_no_gaming(self):
        from engine.strategic.research import _build_search_queries
        queries = _build_search_queries(
            "123 Main St", "Denver", "CO", "Denver",
            "restaurant", "expansion opportunity",
        )
        domains = set(q["domain"] for q in queries)
        assert "demographics" in domains
        # CO not a gaming state + no gaming keywords = no gaming queries
        gaming_queries = [q for q in queries if q["domain"] == "gaming"]
        assert len(gaming_queries) == 0

    def test_format_report_empty(self):
        from engine.strategic.research import format_research_markdown
        md = format_research_markdown({
            "executive_summary": "Test summary",
            "site_score": 7,
            "site_grade": "B",
            "sources_consulted": 15,
            "_meta": {"address": "456 Oak Ave", "elapsed_ms": 5000, "llm_cost_usd": 0.05},
        })
        assert "Market Research Report" in md
        assert "Test summary" in md
        assert "7/10" in md

    def test_format_report_full(self):
        from engine.strategic.research import format_research_markdown
        md = format_research_markdown({
            "executive_summary": "Strong opportunity",
            "site_score": 8,
            "site_grade": "B+",
            "demographics": {
                "population": "115,000",
                "median_income": "$52,000",
                "growth_trend": "stable",
                "key_facts": ["State capital", "University town"],
            },
            "gaming_market": {
                "state_gaming_revenue_trend": "growing",
                "local_terminal_count": "2,400",
                "avg_nti_per_terminal": "$1,200/mo",
                "regulatory_outlook": "favorable",
                "key_facts": ["IL gaming continues to expand"],
            },
            "risk_factors": [
                {"risk": "Market saturation", "severity": "medium", "mitigation": "Focus on high-traffic corridor"},
            ],
            "data_gaps": ["Exact traffic count unavailable"],
            "recommendations": ["Proceed with LOI", "Commission Phase I environmental"],
            "sources_consulted": 18,
            "_meta": {"address": "456 Oak Ave, Springfield, IL", "elapsed_ms": 12000, "llm_cost_usd": 0.12},
        })
        assert "Demographics" in md
        assert "Gaming Market" in md
        assert "Risk Factors" in md
        assert "Data Gaps" in md
        assert "Recommendations" in md

    def test_handler_in_pipeline(self):
        """market_research handler exists in pipeline tool factory."""
        handlers = build_tool_handlers()
        assert "market_research" in handlers
        assert "web_search" in handlers


# ═══════════════════════════════════════════════════════════════
# MARKET RESEARCH
# ═══════════════════════════════════════════════════════════════

class TestMarketResearch:
    def test_query_generation(self):
        from engine.strategic.research import _build_search_queries
        q = _build_search_queries("456 Oak Ave", "Springfield", "IL", "", "gas_station", "gaming terminal evaluation")
        assert len(q) >= 15
        domains = set(r["domain"] for r in q)
        assert "demographics" in domains
        assert "gaming" in domains
        assert "competition" in domains
        assert "real_estate" in domains

    def test_location_parsing(self):
        from engine.strategic.research import _parse_location
        city, state, county = _parse_location("456 Oak Ave, Springfield, IL 62704")
        assert city == "Springfield"
        assert state == "IL"

    def test_report_formatter(self):
        from engine.strategic.research import format_research_markdown
        report = {
            "executive_summary": "Test summary",
            "site_score": 7, "site_grade": "B",
            "demographics": {"population": "100K", "key_facts": ["fact1"]},
            "gaming_market": {"state_gaming_revenue_trend": "growing", "key_facts": []},
            "risk_factors": [{"risk": "test risk", "severity": "high", "mitigation": "mitigate"}],
            "recommendations": ["Do this", "Do that"],
            "data_gaps": ["Missing traffic data"],
            "_meta": {"address": "test", "elapsed_ms": 100, "llm_cost_usd": 0},
        }
        md = format_research_markdown(report)
        assert "Market Research Report" in md
        assert "Demographics" in md
        assert "Risk Factors" in md
        assert "Recommendations" in md

    def test_gaming_queries_for_il(self):
        from engine.strategic.research import _build_search_queries
        q = _build_search_queries("123 Main", "Springfield", "IL", "", "bar", "")
        gaming = [r for r in q if r["domain"] == "gaming"]
        assert len(gaming) >= 3, "IL should trigger gaming queries even without context"

    def test_tool_handler_exists(self):
        handlers = build_tool_handlers()
        assert "market_research" in handlers
        assert "web_search" in handlers

    def test_tool_count(self):
        handlers = build_tool_handlers()
        assert len(handlers) == 10


# ═══════════════════════════════════════════════════════════════
# CONSTRUCTION PIPELINE
# ═══════════════════════════════════════════════════════════════

class TestConstruction:
    def test_cost_estimate(self):
        from engine.construction.costs import estimate_costs
        scope = {
            "project_type": "renovation", "property_type": "gas_station",
            "total_sqft": 2400, "renovation_sqft": 2400, "terminal_count": 6,
            "hvac_tons": 6, "electrical_service": "400A", "plumbing_fixtures": 4,
        }
        est = estimate_costs(scope, state="IL")
        assert est["total_project_cost"] > 0
        assert est["cost_per_sqft"] > 0
        assert len(est["line_items"]) >= 5

    def test_location_factors(self):
        from engine.construction.costs import get_location_factor
        assert get_location_factor("NY", "New York") > get_location_factor("IL")
        assert get_location_factor("TX") < 1.0

    def test_schedule(self):
        from engine.construction.schedule import build_schedule
        scope = {"project_type": "renovation", "total_sqft": 2400, "terminal_count": 6}
        sched = build_schedule(scope)
        assert len(sched["activities"]) >= 15
        assert sched["total_duration_days"] > 0
        gaming = [a for a in sched["activities"] if isinstance(a, dict) and a.get("id", "").startswith("G")]
        assert len(gaming) >= 4

    def test_manpower(self):
        from engine.construction.schedule import build_schedule, manpower_takeoff
        scope = {"project_type": "renovation", "total_sqft": 2400, "terminal_count": 6}
        sched = build_schedule(scope)
        mp = manpower_takeoff(sched, state="IL")
        assert mp["total_man_days"] > 0
        assert mp["total_labor_cost"] > 0

    def test_pipeline_quick(self):
        from engine.construction.pipeline import ConstructionPipeline
        cp = ConstructionPipeline()
        r = cp.quick_estimate(property_type="gas_station", sqft=2400, terminal_count=6)
        assert r["total_project_cost"] > 0
        assert r["go_no_go"] in ("GO", "MODIFY", "NO_GO")
        assert "scope" in r and "schedule" in r and "manpower" in r

    def test_pipeline_over_budget(self):
        from engine.construction.pipeline import ConstructionPipeline
        cp = ConstructionPipeline()
        r = cp.analyze(
            scope={"project_type": "renovation", "property_type": "gas_station",
                   "total_sqft": 5000, "terminal_count": 6},
            state="NY", city="New York", budget=100000,
        )
        assert len(r["risk_factors"]) >= 1  # should flag over budget

    def test_new_build_schedule(self):
        from engine.construction.schedule import build_schedule
        scope = {"project_type": "new_build", "total_sqft": 4000}
        sched = build_schedule(scope)
        assert sched["total_duration_days"] > 50  # new builds take longer


class TestSearchProviders:
    def test_cache(self):
        from engine.strategic.search_providers import SearchCache, SearchResponse
        c = SearchCache(ttl_seconds=60)
        r = SearchResponse(query="test")
        c.set("q", "p", r)
        assert c.get("q", "p") is not None
        assert c.get("missing", "p") is None

    def test_search_result(self):
        from engine.strategic.search_providers import SearchResult
        sr = SearchResult(title="T", url="https://example.com/p", snippet="S")
        assert sr.domain == "example.com"

    def test_multi_search_no_keys(self):
        from engine.strategic.search_providers import multi_search
        r = multi_search("test")
        assert "none_available" in r.providers_used


class TestVectorStore:
    def test_fallback_store_search(self):
        from engine.strategic.vector_store import VectorStore
        vs = VectorStore("test")
        assert not vs.is_connected
        vs.store("market_research", "d1", "Springfield IL gas station", {"state": "IL"})
        results = vs.search("market_research", "Springfield gas", top_k=1)
        assert len(results) >= 1

    def test_trend_point(self):
        from engine.strategic.vector_store import VectorStore
        vs = VectorStore("test")
        vs.store_trend_point("avg_nti", 1200, "Springfield", "IL")
        trends = vs.get_trend_history("avg_nti", "Springfield")
        assert len(trends) >= 1

    def test_cosine_sim(self):
        from engine.strategic.vector_store import _cosine_sim
        assert abs(_cosine_sim([1, 0], [1, 0]) - 1.0) < 0.01
        assert abs(_cosine_sim([1, 0], [0, 1])) < 0.01


class TestToolInventory:
    def test_handler_count(self):
        h = build_tool_handlers()
        assert len(h) >= 17

    def test_schema_count(self):
        assert len(TOOL_SCHEMAS) >= 17

    def test_construction_in_handlers(self):
        h = build_tool_handlers()
        assert "construction_estimate" in h
        assert "construction_feasibility" in h
        assert "news_search" in h
        assert "local_search" in h
        assert "multi_search" in h
        assert "find_similar_sites" in h
        assert "find_construction_comps" in h
