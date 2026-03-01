"""
engine.contracts — Contract Engine + Structure Optimizer
==========================================================
Phase 5: Gaming contract modeling, Monte Carlo simulation,
and deal structure optimization.

Also re-exports the validation system (ContractRegistry, StageContract, etc.)
for backward compatibility with engine/__init__.py imports.
"""

# Re-export validation classes (originally from engine/contracts.py)
from engine.contracts.validation import (
    ContractRegistry, StageContract, DeterministicValidator,
    ValidationResult, Finding, Severity,
    Evidence, EvidenceType, ConfidenceScore,
)

__all__ = [
    "ContractRegistry", "StageContract", "DeterministicValidator",
    "ValidationResult", "Finding", "Severity",
    "Evidence", "EvidenceType", "ConfidenceScore",
]
