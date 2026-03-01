"""
engine.construction.schedule — Schedule & Manpower Engine
============================================================
Generates construction schedules with critical path analysis
and trade-by-trade manpower takeoffs from scope.
"""

from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# ACTIVITY TEMPLATES BY PROJECT TYPE
# ═══════════════════════════════════════════════════════════════

# Duration in days, crew_size = typical crew
ACTIVITY_TEMPLATES = {
    "renovation": [
        {"id": "R01", "name": "Mobilization & protection", "division": "01", "duration": 3, "trade": "general_laborer", "crew": 3, "predecessors": []},
        {"id": "R02", "name": "Selective demolition", "division": "02", "duration": 5, "trade": "general_laborer", "crew": 4, "predecessors": ["R01"]},
        {"id": "R03", "name": "Rough framing", "division": "06", "duration": 7, "trade": "carpenter", "crew": 4, "predecessors": ["R02"]},
        {"id": "R04", "name": "Rough plumbing", "division": "22", "duration": 5, "trade": "plumber", "crew": 2, "predecessors": ["R02"]},
        {"id": "R05", "name": "Rough electrical", "division": "26", "duration": 7, "trade": "electrician", "crew": 3, "predecessors": ["R03"]},
        {"id": "R06", "name": "HVAC rough-in", "division": "23", "duration": 5, "trade": "hvac_tech", "crew": 2, "predecessors": ["R03"]},
        {"id": "R07", "name": "Low voltage rough-in", "division": "27", "duration": 3, "trade": "low_voltage", "crew": 2, "predecessors": ["R03"]},
        {"id": "R08", "name": "Fire protection", "division": "21", "duration": 4, "trade": "fire_protection", "crew": 2, "predecessors": ["R03"]},
        {"id": "R09", "name": "Insulation", "division": "07", "duration": 3, "trade": "insulator", "crew": 2, "predecessors": ["R05", "R06", "R08"]},
        {"id": "R10", "name": "Drywall hang & finish", "division": "09", "duration": 8, "trade": "carpenter", "crew": 4, "predecessors": ["R09"]},
        {"id": "R11", "name": "Painting", "division": "09", "duration": 5, "trade": "painter", "crew": 3, "predecessors": ["R10"]},
        {"id": "R12", "name": "Flooring", "division": "09", "duration": 4, "trade": "tile_setter", "crew": 2, "predecessors": ["R11"]},
        {"id": "R13", "name": "Ceiling grid & tile", "division": "09", "duration": 3, "trade": "carpenter", "crew": 2, "predecessors": ["R11"]},
        {"id": "R14", "name": "Millwork & casework", "division": "06", "duration": 4, "trade": "carpenter", "crew": 2, "predecessors": ["R12"]},
        {"id": "R15", "name": "Plumbing fixtures", "division": "22", "duration": 3, "trade": "plumber", "crew": 2, "predecessors": ["R12"]},
        {"id": "R16", "name": "Electrical trim", "division": "26", "duration": 4, "trade": "electrician", "crew": 2, "predecessors": ["R12", "R13"]},
        {"id": "R17", "name": "HVAC trim & startup", "division": "23", "duration": 3, "trade": "hvac_tech", "crew": 2, "predecessors": ["R13"]},
        {"id": "R18", "name": "Low voltage trim", "division": "27", "duration": 2, "trade": "low_voltage", "crew": 2, "predecessors": ["R16"]},
        {"id": "R19", "name": "Punch list & cleanup", "division": "01", "duration": 3, "trade": "general_laborer", "crew": 3, "predecessors": ["R14", "R15", "R16", "R17", "R18"]},
        {"id": "R20", "name": "Final inspections", "division": "01", "duration": 2, "trade": "superintendent", "crew": 1, "predecessors": ["R19"]},
    ],
    "new_build": [
        {"id": "N01", "name": "Site prep & excavation", "division": "31", "duration": 7, "trade": "equipment_operator", "crew": 3, "predecessors": []},
        {"id": "N02", "name": "Foundation", "division": "03", "duration": 10, "trade": "concrete_finisher", "crew": 5, "predecessors": ["N01"]},
        {"id": "N03", "name": "Structural steel/framing", "division": "05", "duration": 10, "trade": "ironworker", "crew": 4, "predecessors": ["N02"]},
        {"id": "N04", "name": "Roof deck & roofing", "division": "07", "duration": 7, "trade": "roofer", "crew": 4, "predecessors": ["N03"]},
        {"id": "N05", "name": "Exterior skin", "division": "07", "duration": 10, "trade": "carpenter", "crew": 4, "predecessors": ["N03"]},
        {"id": "N06", "name": "Rough plumbing", "division": "22", "duration": 7, "trade": "plumber", "crew": 3, "predecessors": ["N03"]},
        {"id": "N07", "name": "Rough electrical", "division": "26", "duration": 8, "trade": "electrician", "crew": 4, "predecessors": ["N03"]},
        {"id": "N08", "name": "HVAC rough-in", "division": "23", "duration": 7, "trade": "hvac_tech", "crew": 3, "predecessors": ["N04"]},
        {"id": "N09", "name": "Fire protection", "division": "21", "duration": 5, "trade": "fire_protection", "crew": 2, "predecessors": ["N04"]},
        {"id": "N10", "name": "Insulation", "division": "07", "duration": 4, "trade": "insulator", "crew": 3, "predecessors": ["N07", "N08"]},
        {"id": "N11", "name": "Drywall", "division": "09", "duration": 10, "trade": "carpenter", "crew": 5, "predecessors": ["N10"]},
        {"id": "N12", "name": "Painting", "division": "09", "duration": 7, "trade": "painter", "crew": 4, "predecessors": ["N11"]},
        {"id": "N13", "name": "Flooring", "division": "09", "duration": 5, "trade": "tile_setter", "crew": 3, "predecessors": ["N12"]},
        {"id": "N14", "name": "MEP trim", "division": "26", "duration": 7, "trade": "electrician", "crew": 3, "predecessors": ["N12"]},
        {"id": "N15", "name": "Specialties & equipment", "division": "11", "duration": 5, "trade": "carpenter", "crew": 2, "predecessors": ["N13"]},
        {"id": "N16", "name": "Site work & paving", "division": "32", "duration": 7, "trade": "equipment_operator", "crew": 3, "predecessors": ["N05"]},
        {"id": "N17", "name": "Landscaping", "division": "32", "duration": 3, "trade": "general_laborer", "crew": 3, "predecessors": ["N16"]},
        {"id": "N18", "name": "Punch list", "division": "01", "duration": 5, "trade": "general_laborer", "crew": 4, "predecessors": ["N14", "N15", "N17"]},
        {"id": "N19", "name": "Final inspections & CO", "division": "01", "duration": 3, "trade": "superintendent", "crew": 1, "predecessors": ["N18"]},
    ],
    "tenant_improvement": [
        {"id": "T01", "name": "Protection & mobilization", "division": "01", "duration": 2, "trade": "general_laborer", "crew": 2, "predecessors": []},
        {"id": "T02", "name": "Demolition", "division": "02", "duration": 3, "trade": "general_laborer", "crew": 3, "predecessors": ["T01"]},
        {"id": "T03", "name": "Framing", "division": "06", "duration": 5, "trade": "carpenter", "crew": 3, "predecessors": ["T02"]},
        {"id": "T04", "name": "MEP rough-in", "division": "26", "duration": 6, "trade": "electrician", "crew": 3, "predecessors": ["T03"]},
        {"id": "T05", "name": "Insulation & drywall", "division": "09", "duration": 6, "trade": "carpenter", "crew": 3, "predecessors": ["T04"]},
        {"id": "T06", "name": "Paint & finishes", "division": "09", "duration": 5, "trade": "painter", "crew": 2, "predecessors": ["T05"]},
        {"id": "T07", "name": "Flooring", "division": "09", "duration": 3, "trade": "tile_setter", "crew": 2, "predecessors": ["T06"]},
        {"id": "T08", "name": "MEP trim", "division": "26", "duration": 3, "trade": "electrician", "crew": 2, "predecessors": ["T07"]},
        {"id": "T09", "name": "Cleanup & inspection", "division": "01", "duration": 2, "trade": "general_laborer", "crew": 2, "predecessors": ["T08"]},
    ],
}

# Scale factors for sqft (durations scale with size)
def _scale_duration(base_days: int, sqft: float, base_sqft: float = 2000) -> int:
    """Scale activity duration by project size."""
    if sqft <= 0:
        return base_days
    factor = max(0.5, min(3.0, (sqft / base_sqft) ** 0.4))
    return max(1, round(base_days * factor))


# ═══════════════════════════════════════════════════════════════
# GAMING-SPECIFIC ACTIVITIES
# ═══════════════════════════════════════════════════════════════

GAMING_ACTIVITIES = [
    {"id": "G01", "name": "Gaming area partition & framing", "division": "06", "duration": 3, "trade": "carpenter", "crew": 2, "predecessors": []},
    {"id": "G02", "name": "Gaming electrical (dedicated circuits)", "division": "26", "duration": 3, "trade": "electrician", "crew": 2, "predecessors": ["G01"]},
    {"id": "G03", "name": "Gaming data network", "division": "27", "duration": 2, "trade": "low_voltage", "crew": 2, "predecessors": ["G01"]},
    {"id": "G04", "name": "Security cameras (gaming area)", "division": "28", "duration": 2, "trade": "low_voltage", "crew": 1, "predecessors": ["G02"]},
    {"id": "G05", "name": "Gaming terminal installation", "division": "11", "duration": 2, "trade": "low_voltage", "crew": 2, "predecessors": ["G02", "G03"]},
    {"id": "G06", "name": "Gaming board inspection", "division": "01", "duration": 1, "trade": "superintendent", "crew": 1, "predecessors": ["G05"]},
]


# ═══════════════════════════════════════════════════════════════
# SCHEDULE ENGINE
# ═══════════════════════════════════════════════════════════════

def build_schedule(scope: Dict) -> Dict:
    """Generate construction schedule with critical path from scope.

    Returns ConstructionSchedule as dict.
    """
    from engine.construction import ConstructionSchedule, ScheduleActivity

    project_type = scope.get("project_type", "renovation")
    total_sqft = scope.get("total_sqft", 2000)
    terminal_count = scope.get("terminal_count", 0)

    # Get activity template
    templates = ACTIVITY_TEMPLATES.get(project_type, ACTIVITY_TEMPLATES["renovation"])

    # Scale durations for project size
    activities = []
    for t in templates:
        dur = _scale_duration(t["duration"], total_sqft)
        activities.append(ScheduleActivity(
            id=t["id"], name=t["name"], division=t["division"],
            duration_days=dur, predecessors=list(t["predecessors"]),
            trade=t["trade"], crew_size=t["crew"],
        ))

    # Add gaming activities if terminals present
    if terminal_count > 0:
        last_rough_id = None
        for a in activities:
            if "rough" in a.name.lower() and "electrical" in a.name.lower():
                last_rough_id = a.id
        for gt in GAMING_ACTIVITIES:
            preds = list(gt["predecessors"])
            if not preds and last_rough_id:
                preds = [last_rough_id]
            elif preds == [] and activities:
                # Find a reasonable predecessor
                for a in reversed(activities):
                    if a.division in ("09", "06"):
                        preds = [a.id]
                        break
            activities.append(ScheduleActivity(
                id=gt["id"], name=gt["name"], division=gt["division"],
                duration_days=gt["duration"], predecessors=preds,
                trade=gt["trade"], crew_size=gt["crew"],
            ))

    # Forward pass — calculate early start/finish
    activity_map = {a.id: a for a in activities}
    for a in activities:
        if not a.predecessors:
            a.start_day = 0
        else:
            max_end = 0
            for pred_id in a.predecessors:
                pred = activity_map.get(pred_id)
                if pred:
                    max_end = max(max_end, pred.end_day)
            a.start_day = max_end
        a.end_day = a.start_day + a.duration_days

    # Total construction duration
    total_days = max(a.end_day for a in activities) if activities else 0

    # Backward pass — find critical path
    late_finish = {a.id: total_days for a in activities}
    late_start = {}
    for a in reversed(activities):
        # Find successors
        successors = [s for s in activities if a.id in s.predecessors]
        if successors:
            late_finish[a.id] = min(s.start_day for s in successors)
        late_start[a.id] = late_finish[a.id] - a.duration_days
        a.is_critical = (a.start_day == late_start.get(a.id, a.start_day))

    critical_path = [a.id for a in activities if a.is_critical]

    # Trades required
    trades = sorted(set(a.trade for a in activities))

    # Peak workers (by day)
    peak = 0
    for day in range(total_days + 1):
        workers = sum(a.crew_size for a in activities if a.start_day <= day < a.end_day)
        peak = max(peak, workers)

    total_man_days = sum(a.duration_days * a.crew_size for a in activities)

    schedule = ConstructionSchedule(
        project_name=scope.get("project_name", ""),
        activities=activities,
        total_duration_days=total_days,
        total_duration_weeks=(total_days + 4) // 5,
        critical_path=critical_path,
        construction_days=total_days,
        peak_workers=peak,
        total_man_days=total_man_days,
        trades_required=trades,
    )

    return schedule.to_dict()


# ═══════════════════════════════════════════════════════════════
# MANPOWER TAKEOFF
# ═══════════════════════════════════════════════════════════════

def manpower_takeoff(schedule: Dict, state: str = "IL") -> Dict:
    """Generate trade-by-trade manpower requirements from schedule.

    Returns ManpowerTakeoff as dict.
    """
    from engine.construction import ManpowerTakeoff
    from engine.construction.costs import LABOR_RATES, LOCATION_FACTORS

    loc_factor = LOCATION_FACTORS.get(state, 1.00)
    activities = schedule.get("activities", [])

    # Aggregate by trade
    trade_data = {}
    for a in activities:
        trade = a.get("trade", "general_laborer") if isinstance(a, dict) else a.trade
        duration = a.get("duration_days", 0) if isinstance(a, dict) else a.duration_days
        crew = a.get("crew_size", 1) if isinstance(a, dict) else a.crew_size
        man_days = duration * crew

        if trade not in trade_data:
            trade_data[trade] = {"man_days": 0, "peak_crew": 0, "duration_days": 0}
        trade_data[trade]["man_days"] += man_days
        trade_data[trade]["peak_crew"] = max(trade_data[trade]["peak_crew"], crew)
        trade_data[trade]["duration_days"] += duration

    trades = []
    total_man_days = 0
    total_labor_cost = 0
    peak = 0

    for trade, data in sorted(trade_data.items()):
        hourly = LABOR_RATES.get(trade, 45) * loc_factor
        daily = hourly * 8
        cost = data["man_days"] * daily
        trades.append({
            "trade": trade,
            "man_days": data["man_days"],
            "crew_size": data["peak_crew"],
            "duration_days": data["duration_days"],
            "hourly_rate": round(hourly, 2),
            "daily_rate": round(daily, 2),
            "total_cost": round(cost, 2),
        })
        total_man_days += data["man_days"]
        total_labor_cost += cost
        peak = max(peak, data["peak_crew"])

    return ManpowerTakeoff(
        trades=trades,
        total_man_days=total_man_days,
        total_labor_cost=round(total_labor_cost, 2),
        peak_headcount=schedule.get("peak_workers", peak),
    ).to_dict()
