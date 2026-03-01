"""
engine.financial.serialization — JSON Serialization for Tool I/O
=================================================================
Converts dataclass inputs/outputs to/from JSON-safe dicts
for storage in the tool_runs table and API responses.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional


def to_dict(obj: Any) -> Any:
    """Recursively convert a dataclass (or list of dataclasses) to a dict.

    Handles nested dataclasses, lists, and callables (excluded).
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            # Skip callables (e.g., compute_fn in SensitivityInput)
            if callable(val) and not dataclasses.is_dataclass(val):
                continue
            result[f.name] = to_dict(val)
        return result
    elif isinstance(obj, list):
        return [to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    else:
        return obj


def format_amortization_summary(output: Any) -> Dict:
    """Compact summary for amortization (skip full schedule)."""
    d = to_dict(output)
    d["schedule_length"] = len(d.get("schedule", []))
    # Keep first 3 and last 3 payments for preview
    schedule = d.get("schedule", [])
    if len(schedule) > 6:
        d["schedule_preview"] = schedule[:3] + [{"...": "..."}] + schedule[-3:]
        del d["schedule"]
    return d


def format_sensitivity_summary(output: Any) -> Dict:
    """Format sensitivity matrix for readable output."""
    d = to_dict(output)
    # Add min/max for quick scanning
    matrix = d.get("matrix", [])
    if matrix:
        all_vals = [v for row in matrix for v in row]
        d["min_value"] = min(all_vals)
        d["max_value"] = max(all_vals)
        d["base_case_value"] = d.get("base_case_value")
    return d
