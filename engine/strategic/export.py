"""
engine.strategic.export — Memo & Report Export
==================================================
Generates structured text artifacts from strategic analysis results.
Reuses patterns from financial/export.

Outputs: Markdown memo, JSON summary, or CSV actions list.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List


def export_memo_markdown(result: Dict) -> str:
    """Generate a strategic analysis memo in Markdown."""
    title = result.get("title", "Untitled")
    decision = result.get("decision", "—")
    confidence = result.get("confidence", 0)
    rationale = result.get("decision_rationale", "")
    swot = result.get("swot", {})
    scenarios = result.get("scenarios", [])
    failure_modes = result.get("failure_modes", [])
    leverage = result.get("leverage_points", [])
    missing = result.get("missing_info", [])
    contradictions = result.get("contradictions", [])
    second_order = result.get("second_order_effects", [])
    next_actions = result.get("next_actions", [])
    sensitivities = result.get("sensitivities", [])
    stage_routes = result.get("stage_routes", {})
    elapsed = result.get("elapsed_ms", 0)

    badge = {"GO": "✅ GO", "MODIFY": "⚠️ MODIFY", "NO_GO": "❌ NO-GO"}.get(decision, decision)

    lines = [
        f"# Strategic Analysis: {title}",
        "",
        f"**Decision: {badge}** | Confidence: {confidence:.0%} | "
        f"Elapsed: {elapsed}ms",
        "",
        f"> {rationale}",
        "",
    ]

    # SWOT
    if any(swot.get(k) for k in ("strengths", "weaknesses", "opportunities", "threats")):
        lines.append("## SWOT Analysis")
        lines.append("")
        lines.append("| Strengths | Weaknesses |")
        lines.append("|-----------|------------|")
        s = swot.get("strengths", [])
        w = swot.get("weaknesses", [])
        max_sw = max(len(s), len(w), 1)
        for i in range(max_sw):
            si = s[i] if i < len(s) else ""
            wi = w[i] if i < len(w) else ""
            lines.append(f"| {si} | {wi} |")
        lines.append("")
        lines.append("| Opportunities | Threats |")
        lines.append("|---------------|---------|")
        o = swot.get("opportunities", [])
        t = swot.get("threats", [])
        max_ot = max(len(o), len(t), 1)
        for i in range(max_ot):
            oi = o[i] if i < len(o) else ""
            ti = t[i] if i < len(t) else ""
            lines.append(f"| {oi} | {ti} |")
        lines.append("")

    # Scenarios
    if scenarios:
        lines.append("## Scenario Cases")
        lines.append("")
        lines.append("| Case | Probability | Outcome |")
        lines.append("|------|-------------|---------|")
        for sc in scenarios:
            name = sc.get("name", "—")
            prob = sc.get("probability", 0)
            outcome = sc.get("expected_outcome", "—")[:80]
            lines.append(f"| {name.title()} | {prob:.0%} | {outcome} |")
        lines.append("")

    # Sensitivities
    if sensitivities:
        lines.append("## Key Sensitivities")
        lines.append("")
        for s in sensitivities:
            lines.append(f"- {s}")
        lines.append("")

    # Failure Modes
    if failure_modes:
        lines.append("## Failure Modes")
        lines.append("")
        lines.append("| Domain | Description | Probability | Severity | Mitigation |")
        lines.append("|--------|-------------|-------------|----------|------------|")
        for fm in failure_modes:
            lines.append(
                f"| {fm.get('domain', '')} | {fm.get('description', '')[:50]} | "
                f"{fm.get('probability', '')} | {fm.get('severity', '')} | "
                f"{fm.get('mitigation', '')[:50]} |"
            )
        lines.append("")

    # Leverage Points
    if leverage:
        lines.append("## Leverage Points")
        lines.append("")
        for lp in leverage:
            lines.append(f"- {lp}")
        lines.append("")

    # Second-Order Effects
    if second_order:
        lines.append("## Second-Order Effects")
        lines.append("")
        for se in second_order:
            lines.append(f"- {se}")
        lines.append("")

    # Contradictions
    if contradictions:
        lines.append("## ⚠️ Contradictions / Inconsistencies")
        lines.append("")
        for c in contradictions:
            lines.append(f"- {c}")
        lines.append("")

    # Missing Info
    if missing:
        lines.append("## Information Gaps")
        lines.append("")
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")

    # Next Actions
    if next_actions:
        lines.append("## Recommended Next Actions")
        lines.append("")
        lines.append("| # | Action | Owner | Timeline | Priority |")
        lines.append("|---|--------|-------|----------|----------|")
        for i, act in enumerate(next_actions, 1):
            lines.append(
                f"| {i} | {act.get('action', '')[:60]} | "
                f"{act.get('owner', '—')} | {act.get('timeline', '—')} | "
                f"{act.get('priority', '—')} |"
            )
        lines.append("")

    # Phase 7 — LLM Routes used
    if stage_routes:
        lines.append("## Pipeline Routing (Phase 7)")
        lines.append("")
        for stage, route in stage_routes.items():
            lines.append(f"- **{stage}** → `{route}`")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
                 f"by Arkain Strategic Intelligence Layer*")

    return "\n".join(lines)


def export_actions_csv(result: Dict) -> str:
    """Export next actions as CSV."""
    actions = result.get("next_actions", [])
    rows = ["Priority,Action,Owner,Timeline,Dependencies"]
    for act in actions:
        deps = "; ".join(act.get("dependencies", []))
        rows.append(
            f"{act.get('priority', '')},{act.get('action', '').replace(',', ';')},"
            f"{act.get('owner', '')},{act.get('timeline', '')},{deps}"
        )
    return "\n".join(rows)


def export_summary_json(result: Dict) -> str:
    """Export compact JSON summary (for artifact storage)."""
    summary = {
        "title": result.get("title"),
        "decision": result.get("decision"),
        "confidence": result.get("confidence"),
        "scenario_count": len(result.get("scenarios", [])),
        "failure_mode_count": len(result.get("failure_modes", [])),
        "leverage_point_count": len(result.get("leverage_points", [])),
        "gap_count": len(result.get("missing_info", [])),
        "action_count": len(result.get("next_actions", [])),
        "elapsed_ms": result.get("elapsed_ms"),
        "stage_routes": result.get("stage_routes"),
    }
    return json.dumps(summary, indent=2)
