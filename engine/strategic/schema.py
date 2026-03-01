"""
engine.strategic.schema — Strategic Intelligence Layer Models
================================================================
Canonical data models for scenario analysis inputs, outputs,
and intermediate stage results.

Zero external dependencies. Pure dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class Decision(str, Enum):
    GO = "GO"
    MODIFY = "MODIFY"
    NO_GO = "NO_GO"


class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class TimeHorizon(str, Enum):
    SHORT = "short"        # < 6 months
    MEDIUM = "medium"      # 6-24 months
    LONG = "long"          # 2-5 years
    STRATEGIC = "strategic" # 5+ years


class FailureDomain(str, Enum):
    EXECUTION = "execution"
    REGULATORY = "regulatory"
    MARKET = "market"
    PARTNER = "partner"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"


# ═══════════════════════════════════════════════════════════════
# SCENARIO INPUT
# ═══════════════════════════════════════════════════════════════

@dataclass
class ScenarioInput:
    """Everything needed to run a strategic analysis."""

    # Required
    title: str = ""
    scenario_text: str = ""

    # Structured objectives + constraints
    objectives: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)

    # Context
    time_horizon: str = "medium"      # TimeHorizon value
    budget_usd: float = 0.0
    risk_tolerance: str = "moderate"  # RiskTolerance value
    assumptions: List[str] = field(default_factory=list)

    # Optional attachments / references
    related_deal_ids: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # Metadata
    created_by: str = ""
    workspace_id: str = ""

    def validate(self) -> List[str]:
        """Return list of validation errors, empty = valid."""
        errors = []
        if not self.scenario_text.strip():
            errors.append("scenario_text is required")
        if not self.title.strip():
            errors.append("title is required")
        return errors


# ═══════════════════════════════════════════════════════════════
# SWOT
# ═══════════════════════════════════════════════════════════════

@dataclass
class SWOTAnalysis:
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    opportunities: List[str] = field(default_factory=list)
    threats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "opportunities": self.opportunities,
            "threats": self.threats,
        }


# ═══════════════════════════════════════════════════════════════
# SCENARIO CASE
# ═══════════════════════════════════════════════════════════════

@dataclass
class ScenarioCase:
    """One scenario variant (base/bull/bear)."""
    name: str = ""          # "base", "bull", "bear", or custom
    description: str = ""
    probability: float = 0.0  # 0-1
    key_assumptions: List[str] = field(default_factory=list)
    expected_outcome: str = ""
    financial_impact: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "probability": self.probability,
            "key_assumptions": self.key_assumptions,
            "expected_outcome": self.expected_outcome,
            "financial_impact": self.financial_impact,
        }


# ═══════════════════════════════════════════════════════════════
# FAILURE MODE
# ═══════════════════════════════════════════════════════════════

@dataclass
class FailureMode:
    domain: str = ""       # FailureDomain value
    description: str = ""
    probability: str = ""  # high / medium / low
    severity: str = ""     # critical / major / minor
    mitigation: str = ""

    def to_dict(self) -> Dict:
        return {
            "domain": self.domain,
            "description": self.description,
            "probability": self.probability,
            "severity": self.severity,
            "mitigation": self.mitigation,
        }


# ═══════════════════════════════════════════════════════════════
# NEXT ACTION
# ═══════════════════════════════════════════════════════════════

@dataclass
class NextAction:
    action: str = ""
    owner: str = ""
    timeline: str = ""
    priority: str = "medium"  # high / medium / low
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "action": self.action,
            "owner": self.owner,
            "timeline": self.timeline,
            "priority": self.priority,
            "dependencies": self.dependencies,
        }


# ═══════════════════════════════════════════════════════════════
# STAGE RESULTS (intermediate)
# ═══════════════════════════════════════════════════════════════

@dataclass
class CompressionResult:
    """Stage 1: Structured scenario model."""
    structured_scenario: str = ""
    explicit_assumptions: List[str] = field(default_factory=list)
    scope_boundaries: List[str] = field(default_factory=list)
    key_variables: List[str] = field(default_factory=list)
    missing_info: List[str] = field(default_factory=list)
    status: str = "pass"
    errors: List[str] = field(default_factory=list)


@dataclass
class DecisionPrepResult:
    """Stage 2: Decision readiness assessment."""
    decision_criteria: List[str] = field(default_factory=list)
    missing_info: List[str] = field(default_factory=list)
    gating_risks: List[str] = field(default_factory=list)
    preliminary_decision: str = ""  # GO / MODIFY / NO_GO
    triage_rationale: str = ""
    status: str = "pass"
    errors: List[str] = field(default_factory=list)


@dataclass
class ScenariosResult:
    """Stage 3: Scenario simulation."""
    cases: List[ScenarioCase] = field(default_factory=list)
    sensitivities: List[str] = field(default_factory=list)
    second_order_effects: List[str] = field(default_factory=list)
    status: str = "pass"
    errors: List[str] = field(default_factory=list)


@dataclass
class PatternsResult:
    """Stage 4: Pattern recognition."""
    failure_modes: List[FailureMode] = field(default_factory=list)
    leverage_points: List[str] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    analogous_situations: List[str] = field(default_factory=list)
    status: str = "pass"
    errors: List[str] = field(default_factory=list)


@dataclass
class SynthesisResult:
    """Stage 5: Final synthesis."""
    decision: str = ""         # Decision enum value
    confidence: float = 0.0    # 0-1
    swot: SWOTAnalysis = field(default_factory=SWOTAnalysis)
    recommendation: str = ""
    next_actions: List[NextAction] = field(default_factory=list)
    key_findings: List[str] = field(default_factory=list)
    status: str = "pass"
    errors: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# FULL PIPELINE OUTPUT
# ═══════════════════════════════════════════════════════════════

@dataclass
class StrategicAnalysisResult:
    """Complete output of the strategic analysis pipeline."""

    # Identity
    scenario_id: str = ""
    run_id: str = ""
    title: str = ""

    # Decision
    decision: str = ""         # Decision enum value
    confidence: float = 0.0    # 0-1
    decision_rationale: str = ""

    # SWOT
    swot: SWOTAnalysis = field(default_factory=SWOTAnalysis)

    # Scenarios
    scenarios: List[ScenarioCase] = field(default_factory=list)
    sensitivities: List[str] = field(default_factory=list)

    # Risk
    failure_modes: List[FailureMode] = field(default_factory=list)
    second_order_effects: List[str] = field(default_factory=list)

    # Leverage
    leverage_points: List[str] = field(default_factory=list)

    # Gaps
    missing_info: List[str] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)

    # Actions
    next_actions: List[NextAction] = field(default_factory=list)

    # Stage detail
    stage_results: Dict[str, Any] = field(default_factory=dict)

    # LLM routing used per stage
    stage_routes: Dict[str, str] = field(default_factory=dict)

    # Meta
    elapsed_ms: int = 0
    llm_cost_usd: float = 0.0
    status: str = "completed"  # completed, failed, incomplete

    def to_dict(self) -> Dict:
        return {
            "scenario_id": self.scenario_id,
            "run_id": self.run_id,
            "title": self.title,
            "decision": self.decision,
            "confidence": self.confidence,
            "decision_rationale": self.decision_rationale,
            "swot": self.swot.to_dict(),
            "scenarios": [c.to_dict() for c in self.scenarios],
            "sensitivities": self.sensitivities,
            "failure_modes": [f.to_dict() for f in self.failure_modes],
            "second_order_effects": self.second_order_effects,
            "leverage_points": self.leverage_points,
            "missing_info": self.missing_info,
            "contradictions": self.contradictions,
            "next_actions": [a.to_dict() for a in self.next_actions],
            "stage_results": self.stage_results,
            "stage_routes": self.stage_routes,
            "elapsed_ms": self.elapsed_ms,
            "llm_cost_usd": self.llm_cost_usd,
            "status": self.status,
        }
