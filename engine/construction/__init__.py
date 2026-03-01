"""
engine.construction — Construction Document Pipeline
=======================================================
Processes architectural, MEP, and construction documents to produce
cost estimates, schedules, manpower takeoffs, and feasibility
assessments that feed into strategic go/no-go decisions.

Pipeline stages:
  1. Document Intake — parse uploaded plans, specs, BOQs
  2. Scope Extraction — LLM extracts scope from documents
  3. Cost Estimation — RSMeans-benchmarked cost model + web search for local rates
  4. Schedule Build — activity sequencing, critical path, duration
  5. Manpower Takeoff — trade-by-trade labor requirements
  6. Pricing Assembly — total project cost with contingency and soft costs
  7. Feasibility Check — feeds into strategic pipeline for go/no-go

Document types handled:
  - Architectural plans (PDF) → scope, sqft, finishes
  - MEP drawings (PDF) → mechanical/electrical/plumbing scope
  - Specifications (PDF/DOCX) → material specs, quality levels
  - BOQ / Bill of Quantities (XLSX/CSV) → line-item quantities
  - Bid tabs / cost breakdowns (XLSX) → vendor pricing

Integration:
  - VectorStore stores historical cost data for comp-based estimation
  - Web search (Serper) fetches current material prices, labor rates
  - Strategic pipeline calls construction_feasibility as a tool
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass
class ConstructionScope:
    """Extracted scope from construction documents."""
    project_name: str = ""
    project_type: str = ""           # renovation, new_build, tenant_improvement, addition
    property_type: str = ""          # gas_station, restaurant, bar, retail, office
    address: str = ""
    total_sqft: float = 0
    stories: int = 1
    construction_type: str = ""      # Type I-V per IBC
    occupancy_class: str = ""        # A, B, E, M, S, etc.

    # Architectural scope
    demolition_sqft: float = 0
    new_construction_sqft: float = 0
    renovation_sqft: float = 0
    exterior_work: List[str] = field(default_factory=list)    # facade, roof, parking, signage
    interior_finishes: List[str] = field(default_factory=list) # flooring, ceiling, millwork
    ada_compliance: bool = True

    # MEP scope
    hvac_scope: str = ""             # new_system, replace, repair, none
    hvac_tons: float = 0
    electrical_service: str = ""     # 200A, 400A, 600A
    electrical_upgrades: List[str] = field(default_factory=list)
    plumbing_fixtures: int = 0
    plumbing_scope: str = ""         # new_rough, fixture_replace, none
    fire_protection: str = ""        # sprinkler, alarm, suppression, none
    low_voltage: List[str] = field(default_factory=list)       # data, security, AV, POS

    # Gaming-specific
    gaming_area_sqft: float = 0
    terminal_count: int = 0
    gaming_electrical: bool = False  # Dedicated circuits for terminals
    gaming_data: bool = False        # Network infrastructure for terminals

    # Site work
    site_work: List[str] = field(default_factory=list)   # grading, paving, landscaping, utilities
    parking_spaces: int = 0
    fuel_canopy: bool = False
    underground_tanks: bool = False

    # Documents processed
    documents_parsed: List[str] = field(default_factory=list)
    extraction_confidence: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CostLineItem:
    """Single line item in a construction cost estimate."""
    division: str = ""          # CSI division (01-49)
    category: str = ""          # e.g. "Concrete", "Electrical"
    description: str = ""
    quantity: float = 0
    unit: str = ""              # SF, LF, EA, LS
    unit_cost: float = 0
    total_cost: float = 0
    labor_cost: float = 0
    material_cost: float = 0
    source: str = ""            # rsmeans, web_search, historical, user_input

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CostEstimate:
    """Full construction cost estimate."""
    project_name: str = ""
    estimate_date: str = ""
    location: str = ""
    state: str = ""

    # Hard costs
    line_items: List[CostLineItem] = field(default_factory=list)
    hard_cost_subtotal: float = 0

    # Soft costs
    architectural_fees: float = 0      # typically 5-10% of hard
    engineering_fees: float = 0        # typically 3-5% of hard
    permits_fees: float = 0
    inspections: float = 0
    insurance: float = 0
    legal: float = 0
    soft_cost_subtotal: float = 0

    # Contingency
    design_contingency_pct: float = 0.10  # 10% for schematic, 5% for CD
    construction_contingency_pct: float = 0.10
    contingency_total: float = 0

    # Totals
    total_project_cost: float = 0
    cost_per_sqft: float = 0
    total_sqft: float = 0

    # Confidence
    confidence: str = "moderate"  # low, moderate, high
    basis: str = ""              # schematic, design_development, construction_docs, bid

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["line_items"] = [li if isinstance(li, dict) else asdict(li) for li in self.line_items]
        return d


@dataclass
class ScheduleActivity:
    """Single activity in a construction schedule."""
    id: str = ""
    name: str = ""
    division: str = ""
    duration_days: int = 0
    predecessors: List[str] = field(default_factory=list)
    trade: str = ""             # GC, concrete, steel, electrical, plumbing, HVAC, etc.
    crew_size: int = 0
    start_day: int = 0          # calculated
    end_day: int = 0            # calculated
    is_critical: bool = False   # on critical path

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ConstructionSchedule:
    """Full construction schedule with critical path."""
    project_name: str = ""
    activities: List[ScheduleActivity] = field(default_factory=list)
    total_duration_days: int = 0
    total_duration_weeks: int = 0
    critical_path: List[str] = field(default_factory=list)  # activity IDs

    # Phases
    preconstruction_days: int = 30
    permitting_days: int = 45
    construction_days: int = 0
    closeout_days: int = 14

    # Manpower
    peak_workers: int = 0
    total_man_days: int = 0
    trades_required: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["activities"] = [a if isinstance(a, dict) else asdict(a) for a in self.activities]
        return d


@dataclass
class ManpowerTakeoff:
    """Trade-by-trade labor requirements."""
    trades: List[Dict] = field(default_factory=list)  # {trade, man_days, crew_size, duration_days, hourly_rate, total_cost}
    total_man_days: int = 0
    total_labor_cost: float = 0
    peak_headcount: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ConstructionAssessment:
    """Complete construction feasibility assessment for strategic pipeline."""
    project_id: str = ""
    project_name: str = ""
    scope: Optional[ConstructionScope] = None
    cost_estimate: Optional[CostEstimate] = None
    schedule: Optional[ConstructionSchedule] = None
    manpower: Optional[ManpowerTakeoff] = None

    # Feasibility
    feasibility: str = "viable"   # viable, marginal, not_viable
    feasibility_score: float = 0.5  # 0-1
    go_no_go: str = "MODIFY"     # GO, MODIFY, NO_GO

    # Key metrics for strategic pipeline
    total_project_cost: float = 0
    cost_per_sqft: float = 0
    construction_duration_weeks: int = 0
    roi_impact: str = ""          # how construction cost affects deal IRR
    risk_factors: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    # Data quality
    documents_analyzed: int = 0
    web_searches_run: int = 0
    llm_cost_usd: float = 0
    elapsed_ms: int = 0

    def to_dict(self) -> Dict:
        d = {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "feasibility": self.feasibility,
            "feasibility_score": self.feasibility_score,
            "go_no_go": self.go_no_go,
            "total_project_cost": self.total_project_cost,
            "cost_per_sqft": self.cost_per_sqft,
            "construction_duration_weeks": self.construction_duration_weeks,
            "roi_impact": self.roi_impact,
            "risk_factors": self.risk_factors,
            "recommendations": self.recommendations,
            "documents_analyzed": self.documents_analyzed,
            "web_searches_run": self.web_searches_run,
            "llm_cost_usd": self.llm_cost_usd,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.scope:
            d["scope"] = self.scope.to_dict()
        if self.cost_estimate:
            d["cost_estimate"] = self.cost_estimate.to_dict()
        if self.schedule:
            d["schedule"] = self.schedule.to_dict()
        if self.manpower:
            d["manpower"] = self.manpower.to_dict()
        return d
