"""
engine.strategic — Strategic Intelligence Layer (SIL)
========================================================
Production 5-stage cognitive pipeline with:
  - Real LLM reasoning (Anthropic Claude / OpenAI GPT fallback)
  - Live tool execution (EGM predict, Monte Carlo, portfolio, financials)
  - Multi-provider web search (Serper + Anthropic + Google)
  - Deep market research reports (15-20 searches per site)
  - Vector memory (Qdrant) for institutional knowledge + trend tracking
  - Construction pipeline for go/no-go feasibility
  - OODA retry with validation feedback
  - Per-stage model routing (cheap_structured → strategic_deep)
  - Full cost tracking and audit trail

Agents: structuring_analyst, decision_analyst, scenario_analyst,
        pattern_analyst, executive_synthesizer

Strategic tools (17):
  egm_predict, simulate_contract, evaluate_deal, portfolio_dashboard,
  deal_impact, amortize, dscr, egm_market_health, web_search,
  multi_search, news_search, local_search, market_research,
  construction_estimate, construction_feasibility,
  find_similar_sites, find_construction_comps
"""

from engine.strategic.schema import (
    ScenarioInput,
    StrategicAnalysisResult,
    SWOTAnalysis,
    ScenarioCase,
    FailureMode,
    NextAction,
    Decision,
)
from engine.strategic.pipeline import (
    StrategicPipeline,
    DEFAULT_STAGE_ROUTES,
    build_tool_handlers,
)
from engine.strategic.templates import default_scenario_templates, get_template
from engine.strategic.export import export_memo_markdown, export_actions_csv, export_summary_json
from engine.strategic.llm_client import LLMClient, LLMResponse, ToolDefinition
from engine.strategic.stages import TOOL_SCHEMAS
from engine.strategic.research import MarketResearcher, format_research_markdown
from engine.strategic.search_providers import (
    multi_search, quick_search, news_search, local_search,
    SearchResult, SearchResponse, SearchCache,
)
from engine.strategic.vector_store import VectorStore

__all__ = [
    "ScenarioInput",
    "StrategicAnalysisResult",
    "SWOTAnalysis",
    "ScenarioCase",
    "FailureMode",
    "NextAction",
    "Decision",
    "StrategicPipeline",
    "DEFAULT_STAGE_ROUTES",
    "build_tool_handlers",
    "default_scenario_templates",
    "get_template",
    "export_memo_markdown",
    "export_actions_csv",
    "export_summary_json",
    "LLMClient",
    "LLMResponse",
    "ToolDefinition",
    "MarketResearcher",
    "format_research_markdown",
    "TOOL_SCHEMAS",
    "multi_search",
    "quick_search",
    "news_search",
    "local_search",
    "SearchResult",
    "SearchResponse",
    "SearchCache",
    "VectorStore",
]
