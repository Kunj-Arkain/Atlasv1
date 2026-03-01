"""
engine.contracts — Structured Output Contracts & Validators
=============================================================
AUDIT ITEM #6 (Impact: 9/10)

Problems in V1:
  - Stages marked complete by setting {"status": "complete"} without
    proving deliverables are valid (real_estate_pipeline.py:463,564)
  - OODA orient_fn() only checks file existence, not content validity
  - No schema enforcement on any stage output
  - No evidence trail linking claims to data

This module implements:
  - StageContract: what a stage MUST produce to be considered complete
  - DeterministicValidator: rule-based checks (file exists, schema valid,
    field not null, numeric range, min length, regex match, file min size)
  - Evidence: proof that a claim/deliverable is backed by data
  - ConfidenceScore: multi-dimensional quality assessment
  - ContractRegistry: central registry checked after every stage
  - ValidationResult: detailed pass/fail with findings

AUDIT FIX #5 (from "5 immediate fixes"):
  - Never set stage status to complete unless expected files exist
    AND validate against contract

ZERO external dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# EVIDENCE & CONFIDENCE
# ═══════════════════════════════════════════════════════════════

class EvidenceType(str, Enum):
    FILE = "file"
    DATA = "data"
    CITATION = "citation"
    CALCULATION = "calculation"
    API_RESPONSE = "api"


@dataclass
class Evidence:
    """Proof that a deliverable claim is backed by real data."""
    evidence_type: str            # EvidenceType value
    description: str
    source: str = ""              # File path, URL, or reference
    data_hash: str = ""           # SHA256 of evidence data
    verified: bool = False
    verified_by: str = ""         # "deterministic", "agent:name", "human:uid"
    verified_at: Optional[str] = None


@dataclass
class ConfidenceScore:
    """Multi-dimensional quality assessment."""
    overall: float = 0.5          # 0.0 - 1.0
    completeness: float = 0.5
    accuracy: float = 0.5
    freshness: float = 0.5
    source: str = "self"          # "self" (agent), "validator", "human"


# ═══════════════════════════════════════════════════════════════
# VALIDATION FINDINGS
# ═══════════════════════════════════════════════════════════════

class Severity(str, Enum):
    ERROR = "error"         # Blocks completion
    WARNING = "warning"     # Logged but allowed
    INFO = "info"


@dataclass
class Finding:
    """Single finding from a validation check."""
    rule: str
    severity: str           # Severity value
    message: str
    field_name: str = ""
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> Dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "field": self.field_name,
            "expected": str(self.expected) if self.expected is not None else "",
            "actual": str(self.actual) if self.actual is not None else "",
        }


@dataclass
class ValidationResult:
    """Complete result from validating a stage output."""
    stage_name: str
    contract_name: str = ""
    passed: bool = False
    findings: List[Dict] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    confidence: Optional[ConfidenceScore] = None
    validated_at: str = ""
    validator: str = ""

    def __post_init__(self):
        if not self.validated_at:
            self.validated_at = datetime.now(timezone.utc).isoformat()

    def add_finding(self, finding: Finding):
        self.findings.append(finding.to_dict())

    @property
    def errors(self) -> List[Dict]:
        return [f for f in self.findings if f.get("severity") == Severity.ERROR.value]

    @property
    def warnings(self) -> List[Dict]:
        return [f for f in self.findings if f.get("severity") == Severity.WARNING.value]

    @property
    def infos(self) -> List[Dict]:
        return [f for f in self.findings if f.get("severity") == Severity.INFO.value]

    def summary(self) -> Dict:
        return {
            "stage": self.stage_name,
            "passed": self.passed,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "evidence_count": len(self.evidence),
            "validator": self.validator,
        }


# ═══════════════════════════════════════════════════════════════
# STAGE CONTRACT
# ═══════════════════════════════════════════════════════════════

@dataclass
class StageContract:
    """Defines what a stage MUST produce to be considered complete.

    Replaces the old pattern of:
        self.state.market_research = {"status": "complete"}
    with:
        validated, evidence-backed, schema-conforming output.
    """
    name: str
    stage_name: str
    description: str = ""

    # Required files that must exist + be non-empty
    required_files: List[str] = field(default_factory=list)

    # Required state fields that must be non-null/non-empty
    required_state_fields: List[str] = field(default_factory=list)

    # Custom validation rules
    # Format: [{"rule": "min_length", "field": "report", "value": 500}, ...]
    # Supported: not_null, min_length, numeric_range (min/max),
    #            regex_match (pattern), file_min_size (path/min_bytes)
    rules: List[Dict] = field(default_factory=list)

    # Confidence threshold (0.0 = no threshold)
    min_confidence: float = 0.0

    # Evidence requirements
    require_evidence: bool = False
    min_evidence_count: int = 0


# ═══════════════════════════════════════════════════════════════
# DETERMINISTIC VALIDATOR
# ═══════════════════════════════════════════════════════════════

class DeterministicValidator:
    """Rule-based validation of stage outputs.

    Checks:
      1. Required files exist and are non-empty
      2. Required state fields are populated
      3. Custom rules (not_null, min_length, numeric_range, regex, file_min_size)
      4. Evidence requirements met
      5. Confidence threshold met

    Runs BEFORE marking a stage complete. If validation fails,
    the stage either retries (via OODA) or fails explicitly.
    """

    def __init__(self, output_dir: str = ""):
        self._output_dir = Path(output_dir) if output_dir else Path(".")

    def validate(self, contract: StageContract, output: Any = None,
                 state: Any = None,
                 evidence: Optional[List[Evidence]] = None,
                 confidence: Optional[ConfidenceScore] = None) -> ValidationResult:
        """Run all validations against a stage contract."""
        result = ValidationResult(
            stage_name=contract.stage_name,
            contract_name=contract.name,
            validator="deterministic",
        )

        # 1. Required files
        self._check_required_files(contract, result)

        # 2. Required state fields
        self._check_required_state(contract, state, result)

        # 3. Custom rules
        for rule_def in contract.rules:
            self._check_rule(rule_def, output, state, result)

        # 4. Evidence requirements
        all_evidence = list(result.evidence) + (evidence or [])
        if contract.require_evidence and len(all_evidence) < contract.min_evidence_count:
            result.add_finding(Finding(
                rule="min_evidence",
                severity=Severity.ERROR.value,
                message=(
                    f"Need {contract.min_evidence_count} evidence objects, "
                    f"got {len(all_evidence)}"
                ),
                expected=contract.min_evidence_count,
                actual=len(all_evidence),
            ))
        result.evidence = all_evidence

        # 5. Confidence threshold
        if confidence:
            result.confidence = confidence
            if confidence.overall < contract.min_confidence:
                result.add_finding(Finding(
                    rule="min_confidence",
                    severity=Severity.WARNING.value,
                    message=(
                        f"Confidence {confidence.overall:.2f} below "
                        f"threshold {contract.min_confidence:.2f}"
                    ),
                    expected=contract.min_confidence,
                    actual=confidence.overall,
                ))

        # Pass/fail
        result.passed = len(result.errors) == 0
        return result

    def _check_required_files(self, contract: StageContract,
                               result: ValidationResult):
        for rel_path in contract.required_files:
            full = self._output_dir / rel_path
            if not full.exists():
                result.add_finding(Finding(
                    rule="required_file",
                    severity=Severity.ERROR.value,
                    message=f"Required file missing: {rel_path}",
                    field_name=rel_path,
                ))
            elif full.stat().st_size == 0:
                result.add_finding(Finding(
                    rule="file_not_empty",
                    severity=Severity.ERROR.value,
                    message=f"Required file is empty: {rel_path}",
                    field_name=rel_path,
                ))
            else:
                result.evidence.append(Evidence(
                    evidence_type=EvidenceType.FILE.value,
                    description=f"File exists: {rel_path}",
                    source=str(full),
                    verified=True,
                    verified_by="deterministic",
                    verified_at=datetime.now(timezone.utc).isoformat(),
                ))

    def _check_required_state(self, contract: StageContract,
                               state: Any, result: ValidationResult):
        if state is None:
            return
        for field_name in contract.required_state_fields:
            value = None
            if isinstance(state, dict):
                value = state.get(field_name)
            elif hasattr(state, field_name):
                value = getattr(state, field_name)

            if value is None or value == "" or value == {} or value == []:
                result.add_finding(Finding(
                    rule="required_state_field",
                    severity=Severity.ERROR.value,
                    message=f"Required state field is empty: {field_name}",
                    field_name=field_name,
                ))

    def _check_rule(self, rule_def: Dict, output: Any, state: Any,
                     result: ValidationResult):
        rule_type = rule_def.get("rule", "")
        field_name = rule_def.get("field", "")

        # Resolve actual value
        actual = self._resolve_value(field_name, output, state)

        if rule_type == "not_null":
            if actual is None:
                result.add_finding(Finding(
                    rule="not_null", severity=Severity.ERROR.value,
                    message=f"Field '{field_name}' is null",
                    field_name=field_name,
                ))

        elif rule_type == "min_length":
            expected = rule_def.get("value", 0)
            actual_len = len(str(actual)) if actual is not None else 0
            if actual_len < expected:
                result.add_finding(Finding(
                    rule="min_length", severity=Severity.ERROR.value,
                    message=f"Field '{field_name}' length {actual_len} < {expected}",
                    field_name=field_name, expected=expected, actual=actual_len,
                ))

        elif rule_type == "numeric_range":
            min_val = rule_def.get("min")
            max_val = rule_def.get("max")
            if actual is not None:
                try:
                    num = float(actual)
                    if min_val is not None and num < min_val:
                        result.add_finding(Finding(
                            rule="numeric_range", severity=Severity.ERROR.value,
                            message=f"Field '{field_name}' = {num} < min {min_val}",
                            field_name=field_name, expected=f">={min_val}", actual=num,
                        ))
                    if max_val is not None and num > max_val:
                        result.add_finding(Finding(
                            rule="numeric_range", severity=Severity.ERROR.value,
                            message=f"Field '{field_name}' = {num} > max {max_val}",
                            field_name=field_name, expected=f"<={max_val}", actual=num,
                        ))
                except (TypeError, ValueError):
                    result.add_finding(Finding(
                        rule="numeric_range", severity=Severity.ERROR.value,
                        message=f"Field '{field_name}' is not numeric",
                        field_name=field_name,
                    ))
            elif min_val is not None:
                result.add_finding(Finding(
                    rule="numeric_range", severity=Severity.ERROR.value,
                    message=f"Field '{field_name}' is null (expected numeric)",
                    field_name=field_name,
                ))

        elif rule_type == "regex_match":
            pattern = rule_def.get("pattern", "")
            if actual is None or not re.search(pattern, str(actual)):
                result.add_finding(Finding(
                    rule="regex_match", severity=Severity.ERROR.value,
                    message=f"Field '{field_name}' does not match /{pattern}/",
                    field_name=field_name, expected=pattern,
                ))

        elif rule_type == "file_min_size":
            fpath = rule_def.get("path", "")
            min_bytes = rule_def.get("min_bytes", 100)
            full = self._output_dir / fpath
            if not full.exists():
                result.add_finding(Finding(
                    rule="file_min_size", severity=Severity.ERROR.value,
                    message=f"File '{fpath}' does not exist",
                    field_name=fpath,
                ))
            elif full.stat().st_size < min_bytes:
                result.add_finding(Finding(
                    rule="file_min_size", severity=Severity.ERROR.value,
                    message=(
                        f"File '{fpath}' is {full.stat().st_size} bytes "
                        f"(min: {min_bytes})"
                    ),
                    field_name=fpath, expected=min_bytes,
                    actual=full.stat().st_size,
                ))

    def _resolve_value(self, field_name: str, output: Any, state: Any) -> Any:
        """Resolve a field value from output dict or state object."""
        if field_name and isinstance(output, dict):
            val = output.get(field_name)
            if val is not None:
                return val
        if field_name and state is not None:
            if isinstance(state, dict):
                return state.get(field_name)
            if hasattr(state, field_name):
                return getattr(state, field_name)
        return output if not field_name else None


# ═══════════════════════════════════════════════════════════════
# CONTRACT REGISTRY
# ═══════════════════════════════════════════════════════════════

class ContractRegistry:
    """Central registry of all stage contracts.

    Pipelines register contracts at construction time.
    The runtime checks contracts after every stage completion.
    """

    def __init__(self):
        self._contracts: Dict[str, StageContract] = {}
        self._validator = DeterministicValidator()

    def register(self, contract: StageContract):
        self._contracts[contract.stage_name] = contract

    def get(self, stage_name: str) -> Optional[StageContract]:
        return self._contracts.get(stage_name)

    def set_output_dir(self, output_dir: str):
        self._validator = DeterministicValidator(output_dir)

    def validate_stage(self, stage_name: str, output: Any = None,
                       state: Any = None,
                       evidence: Optional[List[Evidence]] = None,
                       confidence: Optional[ConfidenceScore] = None
                       ) -> ValidationResult:
        """Validate a stage's output against its registered contract.

        If no contract registered, returns a passing result with info note.
        """
        contract = self._contracts.get(stage_name)
        if not contract:
            return ValidationResult(
                stage_name=stage_name,
                passed=True,
                findings=[{
                    "rule": "no_contract", "severity": "info",
                    "message": "No contract registered for this stage",
                    "field": "", "expected": "", "actual": "",
                }],
                validator="none",
            )
        return self._validator.validate(
            contract, output, state=state,
            evidence=evidence, confidence=confidence,
        )

    def all_contracts(self) -> Dict[str, StageContract]:
        return dict(self._contracts)
