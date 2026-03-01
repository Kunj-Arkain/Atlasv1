"""
engine.contracts.strategic_contracts — Strategic Pipeline Contracts
=====================================================================
Stage contracts for the Strategic Intelligence Layer.
Uses existing StageContract/ContractRegistry interface.
"""

from __future__ import annotations

from engine.contracts.validation import StageContract, ContractRegistry


def register_strategic_contracts(registry: ContractRegistry):
    """Register all SIL stage contracts with the contract registry."""

    registry.register(StageContract(
        name="strategic_intake",
        stage_name="strategic.intake",
        description="Validate scenario input data",
        required_state_fields=["scenario_text", "title"],
        rules=[
            {"rule": "not_null", "field": "scenario_text"},
            {"rule": "min_length", "field": "scenario_text", "value": 10},
            {"rule": "not_null", "field": "title"},
        ],
    ))

    registry.register(StageContract(
        name="strategic_compression",
        stage_name="strategic.compression",
        description="Structured scenario decomposition",
        required_state_fields=["structured_scenario", "key_variables"],
        rules=[
            {"rule": "not_null", "field": "structured_scenario"},
            {"rule": "min_length", "field": "structured_scenario", "value": 20},
            {"rule": "not_null", "field": "key_variables"},
        ],
    ))

    registry.register(StageContract(
        name="strategic_decision_prep",
        stage_name="strategic.decision_prep",
        description="Decision readiness assessment",
        required_state_fields=["decision_criteria", "preliminary_decision"],
        rules=[
            {"rule": "not_null", "field": "decision_criteria"},
            {"rule": "not_null", "field": "preliminary_decision"},
        ],
    ))

    registry.register(StageContract(
        name="strategic_scenarios",
        stage_name="strategic.scenarios",
        description="Scenario generation with sensitivities",
        required_state_fields=["cases", "sensitivities"],
        rules=[
            {"rule": "not_null", "field": "cases"},
            {"rule": "not_null", "field": "sensitivities"},
        ],
    ))

    registry.register(StageContract(
        name="strategic_patterns",
        stage_name="strategic.patterns",
        description="Failure modes, leverage, contradictions",
        required_state_fields=["failure_modes", "leverage_points"],
        rules=[
            {"rule": "not_null", "field": "failure_modes"},
            {"rule": "not_null", "field": "leverage_points"},
        ],
    ))

    registry.register(StageContract(
        name="strategic_synthesis",
        stage_name="strategic.synthesis",
        description="Final decision, SWOT, recommendations",
        required_state_fields=["decision", "confidence", "swot", "next_actions"],
        rules=[
            {"rule": "not_null", "field": "decision"},
            {"rule": "not_null", "field": "confidence"},
            {"rule": "numeric_range", "field": "confidence", "min": 0.0, "max": 1.0},
            {"rule": "not_null", "field": "swot"},
            {"rule": "not_null", "field": "next_actions"},
        ],
    ))
