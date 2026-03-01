"""
engine.construction.costs — Cost Estimation Engine
=====================================================
RSMeans-benchmarked cost data with location adjustments,
web search for current pricing, and historical comp lookup.

Sources (layered):
  1. Built-in RSMeans-style unit costs (baseline)
  2. Web search for current local rates (Serper)
  3. Historical data from Qdrant vector store
  4. User-provided bid data (highest priority)

CSI MasterFormat divisions:
  01 - General Requirements
  02 - Existing Conditions (demo)
  03 - Concrete
  04 - Masonry
  05 - Metals
  06 - Wood/Plastics/Composites
  07 - Thermal/Moisture Protection
  08 - Openings (doors/windows)
  09 - Finishes
  10 - Specialties
  11 - Equipment
  12 - Furnishings
  21 - Fire Suppression
  22 - Plumbing
  23 - HVAC
  26 - Electrical
  27 - Communications
  28 - Electronic Safety/Security
  31 - Earthwork
  32 - Exterior Improvements
  33 - Utilities
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# LOCATION COST FACTORS (relative to national average = 1.00)
# ═══════════════════════════════════════════════════════════════

LOCATION_FACTORS = {
    # Major metros
    "IL": 1.04, "NY": 1.28, "CA": 1.22, "TX": 0.88, "FL": 0.92,
    "PA": 1.06, "OH": 0.96, "MI": 1.00, "GA": 0.89, "NC": 0.87,
    "NJ": 1.18, "VA": 0.95, "WA": 1.10, "AZ": 0.93, "MA": 1.20,
    "TN": 0.86, "IN": 0.94, "MO": 0.98, "MD": 0.99, "WI": 1.01,
    "CO": 0.98, "MN": 1.05, "SC": 0.84, "AL": 0.85, "LA": 0.90,
    "KY": 0.90, "OR": 1.05, "OK": 0.85, "CT": 1.15, "UT": 0.92,
    "NV": 1.05, "IA": 0.93, "AR": 0.83, "MS": 0.82, "KS": 0.90,
    "NE": 0.90, "NM": 0.91, "ID": 0.92, "WV": 0.96, "HI": 1.35,
    "NH": 1.02, "ME": 0.95, "MT": 0.93, "RI": 1.08, "DE": 1.03,
    "SD": 0.85, "ND": 0.90, "AK": 1.30, "VT": 0.97, "WY": 0.89,
}

# City-level adjustments (multiplied with state factor)
CITY_FACTORS = {
    "Chicago": 1.12, "New York": 1.35, "Los Angeles": 1.18,
    "San Francisco": 1.30, "Seattle": 1.12, "Boston": 1.15,
    "Springfield": 0.92, "Peoria": 0.90, "Rockford": 0.93,
    "Las Vegas": 1.02, "Phoenix": 0.95, "Denver": 1.04,
    "Nashville": 0.93, "Atlanta": 0.95, "Dallas": 0.92,
    "Houston": 0.90, "Miami": 1.02,
}


# ═══════════════════════════════════════════════════════════════
# UNIT COST DATABASE (RSMeans-style, 2024-2025 baseline)
# ═══════════════════════════════════════════════════════════════

# Per-sqft costs by project type and scope
COST_PER_SQFT = {
    # ── New Construction ──────────────────────────────────
    "new_build": {
        "gas_station": {"low": 180, "mid": 240, "high": 320},
        "convenience_store": {"low": 160, "mid": 220, "high": 290},
        "restaurant": {"low": 250, "mid": 350, "high": 500},
        "bar": {"low": 200, "mid": 300, "high": 420},
        "retail": {"low": 120, "mid": 180, "high": 260},
        "office": {"low": 150, "mid": 220, "high": 320},
        "warehouse": {"low": 80, "mid": 120, "high": 180},
        "mixed_use": {"low": 180, "mid": 260, "high": 380},
    },
    # ── Renovation ────────────────────────────────────────
    "renovation": {
        "gas_station": {"low": 80, "mid": 140, "high": 220},
        "convenience_store": {"low": 70, "mid": 120, "high": 190},
        "restaurant": {"low": 120, "mid": 200, "high": 320},
        "bar": {"low": 100, "mid": 180, "high": 280},
        "retail": {"low": 60, "mid": 100, "high": 170},
        "office": {"low": 70, "mid": 120, "high": 200},
        "warehouse": {"low": 40, "mid": 70, "high": 120},
        "mixed_use": {"low": 90, "mid": 150, "high": 240},
    },
    # ── Tenant Improvement ────────────────────────────────
    "tenant_improvement": {
        "gas_station": {"low": 50, "mid": 90, "high": 150},
        "convenience_store": {"low": 45, "mid": 80, "high": 130},
        "restaurant": {"low": 80, "mid": 150, "high": 250},
        "bar": {"low": 70, "mid": 130, "high": 220},
        "retail": {"low": 35, "mid": 65, "high": 120},
        "office": {"low": 40, "mid": 75, "high": 140},
        "mixed_use": {"low": 55, "mid": 95, "high": 160},
    },
}

# CSI Division unit costs (per unit, national average)
DIVISION_COSTS = {
    "02_demolition": {
        "selective_demo": {"unit": "SF", "labor": 3.50, "material": 0.50, "total": 4.00},
        "full_gut": {"unit": "SF", "labor": 6.00, "material": 1.00, "total": 7.00},
        "hazmat_abatement": {"unit": "SF", "labor": 8.00, "material": 4.00, "total": 12.00},
    },
    "03_concrete": {
        "slab_on_grade_4in": {"unit": "SF", "labor": 3.50, "material": 4.50, "total": 8.00},
        "foundation_wall": {"unit": "LF", "labor": 45.00, "material": 55.00, "total": 100.00},
        "sidewalk_4in": {"unit": "SF", "labor": 3.00, "material": 4.00, "total": 7.00},
    },
    "05_metals": {
        "structural_steel": {"unit": "TON", "labor": 1800, "material": 3200, "total": 5000},
        "metal_studs": {"unit": "SF", "labor": 2.50, "material": 1.80, "total": 4.30},
        "misc_metals": {"unit": "LB", "labor": 1.50, "material": 2.00, "total": 3.50},
    },
    "06_wood": {
        "wood_framing": {"unit": "SF", "labor": 4.00, "material": 3.50, "total": 7.50},
        "millwork_standard": {"unit": "LF", "labor": 25.00, "material": 35.00, "total": 60.00},
        "millwork_custom": {"unit": "LF", "labor": 40.00, "material": 80.00, "total": 120.00},
    },
    "07_thermal_moisture": {
        "roof_tpo_single_ply": {"unit": "SF", "labor": 3.50, "material": 4.50, "total": 8.00},
        "roof_metal_standing_seam": {"unit": "SF", "labor": 5.00, "material": 8.00, "total": 13.00},
        "insulation_batt_r19": {"unit": "SF", "labor": 0.80, "material": 1.20, "total": 2.00},
        "waterproofing": {"unit": "SF", "labor": 2.00, "material": 3.00, "total": 5.00},
    },
    "08_openings": {
        "storefront_aluminum": {"unit": "SF", "labor": 18.00, "material": 32.00, "total": 50.00},
        "hollow_metal_door": {"unit": "EA", "labor": 250, "material": 650, "total": 900},
        "overhead_door_12x14": {"unit": "EA", "labor": 800, "material": 3200, "total": 4000},
    },
    "09_finishes": {
        "drywall_paint": {"unit": "SF", "labor": 3.50, "material": 1.50, "total": 5.00},
        "ceramic_tile_floor": {"unit": "SF", "labor": 5.00, "material": 6.00, "total": 11.00},
        "vinyl_plank": {"unit": "SF", "labor": 2.00, "material": 4.00, "total": 6.00},
        "epoxy_floor": {"unit": "SF", "labor": 2.50, "material": 3.50, "total": 6.00},
        "act_ceiling": {"unit": "SF", "labor": 2.00, "material": 2.50, "total": 4.50},
    },
    "11_equipment": {
        "gaming_terminal_install": {"unit": "EA", "labor": 350, "material": 150, "total": 500},
        "commercial_kitchen": {"unit": "LS", "labor": 15000, "material": 45000, "total": 60000},
        "fuel_dispensers": {"unit": "EA", "labor": 2500, "material": 12000, "total": 14500},
    },
    "21_fire_suppression": {
        "wet_sprinkler": {"unit": "SF", "labor": 2.50, "material": 3.50, "total": 6.00},
        "fire_alarm": {"unit": "SF", "labor": 1.50, "material": 2.00, "total": 3.50},
        "hood_suppression": {"unit": "EA", "labor": 1500, "material": 3500, "total": 5000},
    },
    "22_plumbing": {
        "fixture_rough_complete": {"unit": "EA", "labor": 1200, "material": 800, "total": 2000},
        "water_heater_commercial": {"unit": "EA", "labor": 1500, "material": 3500, "total": 5000},
        "grease_trap": {"unit": "EA", "labor": 2000, "material": 3000, "total": 5000},
    },
    "23_hvac": {
        "rooftop_unit_per_ton": {"unit": "TON", "labor": 800, "material": 1700, "total": 2500},
        "split_system_per_ton": {"unit": "TON", "labor": 600, "material": 1400, "total": 2000},
        "ductwork": {"unit": "LB", "labor": 4.00, "material": 3.50, "total": 7.50},
        "exhaust_fan": {"unit": "EA", "labor": 500, "material": 1200, "total": 1700},
    },
    "26_electrical": {
        "service_200a": {"unit": "EA", "labor": 2500, "material": 3500, "total": 6000},
        "service_400a": {"unit": "EA", "labor": 4000, "material": 6000, "total": 10000},
        "service_600a": {"unit": "EA", "labor": 6000, "material": 9000, "total": 15000},
        "panel_200a": {"unit": "EA", "labor": 1200, "material": 1800, "total": 3000},
        "receptacle_20a": {"unit": "EA", "labor": 85, "material": 45, "total": 130},
        "lighting_led_2x4": {"unit": "EA", "labor": 75, "material": 120, "total": 195},
        "gaming_circuit_dedicated": {"unit": "EA", "labor": 350, "material": 200, "total": 550},
    },
    "27_communications": {
        "data_drop_cat6": {"unit": "EA", "labor": 150, "material": 100, "total": 250},
        "wifi_ap": {"unit": "EA", "labor": 200, "material": 350, "total": 550},
        "security_camera": {"unit": "EA", "labor": 200, "material": 400, "total": 600},
        "pos_rough_in": {"unit": "EA", "labor": 250, "material": 150, "total": 400},
    },
    "31_earthwork": {
        "excavation": {"unit": "CY", "labor": 8.00, "material": 0, "total": 8.00},
        "backfill_compacted": {"unit": "CY", "labor": 6.00, "material": 12.00, "total": 18.00},
        "grading": {"unit": "SF", "labor": 0.80, "material": 0, "total": 0.80},
    },
    "32_exterior": {
        "asphalt_paving": {"unit": "SF", "labor": 2.50, "material": 3.50, "total": 6.00},
        "concrete_curb": {"unit": "LF", "labor": 12.00, "material": 8.00, "total": 20.00},
        "striping": {"unit": "LF", "labor": 0.30, "material": 0.20, "total": 0.50},
        "landscaping_basic": {"unit": "SF", "labor": 2.00, "material": 3.00, "total": 5.00},
        "fuel_canopy": {"unit": "EA", "labor": 15000, "material": 35000, "total": 50000},
    },
    "33_utilities": {
        "water_service": {"unit": "LF", "labor": 25.00, "material": 20.00, "total": 45.00},
        "sewer_service": {"unit": "LF", "labor": 30.00, "material": 25.00, "total": 55.00},
        "gas_service": {"unit": "LF", "labor": 20.00, "material": 15.00, "total": 35.00},
        "ust_removal": {"unit": "EA", "labor": 8000, "material": 2000, "total": 10000},
        "ust_install": {"unit": "EA", "labor": 12000, "material": 25000, "total": 37000},
    },
}

# Labor rates by trade (national average $/hr, loaded)
LABOR_RATES = {
    "general_laborer": 38, "carpenter": 52, "electrician": 65,
    "plumber": 62, "hvac_tech": 60, "ironworker": 58,
    "concrete_finisher": 48, "roofer": 50, "painter": 42,
    "tile_setter": 55, "glazier": 56, "insulator": 48,
    "sheet_metal": 58, "fire_protection": 62, "low_voltage": 55,
    "equipment_operator": 55, "superintendent": 75, "project_manager": 85,
}


# ═══════════════════════════════════════════════════════════════
# COST ESTIMATION ENGINE
# ═══════════════════════════════════════════════════════════════

def estimate_costs(
    scope: Dict,
    state: str = "IL",
    city: str = "",
    quality: str = "mid",  # low, mid, high
) -> Dict:
    """Generate a construction cost estimate from scope.

    Args:
        scope: ConstructionScope as dict
        state: US state code for location factor
        city: City name for city-level adjustment
        quality: Construction quality level

    Returns:
        CostEstimate as dict
    """
    from engine.construction import CostEstimate, CostLineItem

    loc_factor = LOCATION_FACTORS.get(state, 1.00)
    city_factor = CITY_FACTORS.get(city, 1.00)
    combined_factor = loc_factor * city_factor

    project_type = scope.get("project_type", "renovation")
    property_type = scope.get("property_type", "gas_station")
    total_sqft = scope.get("total_sqft", 0)

    line_items = []

    # ── Method 1: Per-sqft baseline ──────────────────────
    sqft_costs = COST_PER_SQFT.get(project_type, COST_PER_SQFT["renovation"])
    type_costs = sqft_costs.get(property_type, sqft_costs.get("retail", {"low": 60, "mid": 100, "high": 170}))
    base_per_sqft = type_costs.get(quality, type_costs["mid"])
    adjusted_per_sqft = base_per_sqft * combined_factor

    # ── Method 2: Bottom-up line items ───────────────────
    # Demolition
    demo_sqft = scope.get("demolition_sqft", 0)
    if demo_sqft > 0:
        demo_cost = DIVISION_COSTS["02_demolition"]["selective_demo"]
        line_items.append(CostLineItem(
            division="02", category="Demolition", description="Selective demolition",
            quantity=demo_sqft, unit="SF",
            unit_cost=demo_cost["total"] * combined_factor,
            total_cost=demo_sqft * demo_cost["total"] * combined_factor,
            labor_cost=demo_sqft * demo_cost["labor"] * combined_factor,
            material_cost=demo_sqft * demo_cost["material"] * combined_factor,
            source="rsmeans",
        ))

    # Finishes
    reno_sqft = scope.get("renovation_sqft", 0) or scope.get("new_construction_sqft", 0) or total_sqft
    if reno_sqft > 0:
        finish = DIVISION_COSTS["09_finishes"]["drywall_paint"]
        line_items.append(CostLineItem(
            division="09", category="Finishes", description="Drywall & paint (walls/ceiling)",
            quantity=reno_sqft * 2.5,  # wall area ≈ 2.5x floor
            unit="SF", unit_cost=finish["total"] * combined_factor,
            total_cost=reno_sqft * 2.5 * finish["total"] * combined_factor,
            labor_cost=reno_sqft * 2.5 * finish["labor"] * combined_factor,
            material_cost=reno_sqft * 2.5 * finish["material"] * combined_factor,
            source="rsmeans",
        ))

        flooring = DIVISION_COSTS["09_finishes"]["vinyl_plank"]
        line_items.append(CostLineItem(
            division="09", category="Finishes", description="Flooring (vinyl plank)",
            quantity=reno_sqft, unit="SF",
            unit_cost=flooring["total"] * combined_factor,
            total_cost=reno_sqft * flooring["total"] * combined_factor,
            labor_cost=reno_sqft * flooring["labor"] * combined_factor,
            material_cost=reno_sqft * flooring["material"] * combined_factor,
            source="rsmeans",
        ))

        ceiling = DIVISION_COSTS["09_finishes"]["act_ceiling"]
        line_items.append(CostLineItem(
            division="09", category="Finishes", description="ACT ceiling",
            quantity=reno_sqft, unit="SF",
            unit_cost=ceiling["total"] * combined_factor,
            total_cost=reno_sqft * ceiling["total"] * combined_factor,
            labor_cost=reno_sqft * ceiling["labor"] * combined_factor,
            material_cost=reno_sqft * ceiling["material"] * combined_factor,
            source="rsmeans",
        ))

    # HVAC
    hvac_tons = scope.get("hvac_tons", 0)
    if hvac_tons > 0:
        hvac = DIVISION_COSTS["23_hvac"]["rooftop_unit_per_ton"]
        line_items.append(CostLineItem(
            division="23", category="HVAC", description="HVAC system (rooftop unit)",
            quantity=hvac_tons, unit="TON",
            unit_cost=hvac["total"] * combined_factor,
            total_cost=hvac_tons * hvac["total"] * combined_factor,
            labor_cost=hvac_tons * hvac["labor"] * combined_factor,
            material_cost=hvac_tons * hvac["material"] * combined_factor,
            source="rsmeans",
        ))

    # Electrical service
    elec_service = scope.get("electrical_service", "200A")
    if elec_service:
        svc_key = f"service_{elec_service.lower().replace('a', 'a')}"
        elec = DIVISION_COSTS["26_electrical"].get(svc_key, DIVISION_COSTS["26_electrical"]["service_200a"])
        line_items.append(CostLineItem(
            division="26", category="Electrical", description=f"Electrical service ({elec_service})",
            quantity=1, unit="EA",
            unit_cost=elec["total"] * combined_factor,
            total_cost=elec["total"] * combined_factor,
            labor_cost=elec["labor"] * combined_factor,
            material_cost=elec["material"] * combined_factor,
            source="rsmeans",
        ))

    # Gaming circuits
    terminal_count = scope.get("terminal_count", 0)
    if terminal_count > 0:
        gc = DIVISION_COSTS["26_electrical"]["gaming_circuit_dedicated"]
        line_items.append(CostLineItem(
            division="26", category="Electrical", description="Dedicated gaming circuits",
            quantity=terminal_count, unit="EA",
            unit_cost=gc["total"] * combined_factor,
            total_cost=terminal_count * gc["total"] * combined_factor,
            labor_cost=terminal_count * gc["labor"] * combined_factor,
            material_cost=terminal_count * gc["material"] * combined_factor,
            source="rsmeans",
        ))

        # Gaming terminal installation
        gi = DIVISION_COSTS["11_equipment"]["gaming_terminal_install"]
        line_items.append(CostLineItem(
            division="11", category="Equipment", description="Gaming terminal installation",
            quantity=terminal_count, unit="EA",
            unit_cost=gi["total"] * combined_factor,
            total_cost=terminal_count * gi["total"] * combined_factor,
            labor_cost=terminal_count * gi["labor"] * combined_factor,
            material_cost=terminal_count * gi["material"] * combined_factor,
            source="rsmeans",
        ))

        # Data drops for terminals
        dd = DIVISION_COSTS["27_communications"]["data_drop_cat6"]
        line_items.append(CostLineItem(
            division="27", category="Low Voltage", description="Data drops (Cat6) for terminals",
            quantity=terminal_count, unit="EA",
            unit_cost=dd["total"] * combined_factor,
            total_cost=terminal_count * dd["total"] * combined_factor,
            labor_cost=terminal_count * dd["labor"] * combined_factor,
            material_cost=terminal_count * dd["material"] * combined_factor,
            source="rsmeans",
        ))

    # Plumbing
    fixtures = scope.get("plumbing_fixtures", 0)
    if fixtures > 0:
        fix = DIVISION_COSTS["22_plumbing"]["fixture_rough_complete"]
        line_items.append(CostLineItem(
            division="22", category="Plumbing", description="Plumbing fixtures (rough + finish)",
            quantity=fixtures, unit="EA",
            unit_cost=fix["total"] * combined_factor,
            total_cost=fixtures * fix["total"] * combined_factor,
            labor_cost=fixtures * fix["labor"] * combined_factor,
            material_cost=fixtures * fix["material"] * combined_factor,
            source="rsmeans",
        ))

    # Fire suppression
    fire = scope.get("fire_protection", "")
    if fire == "sprinkler" and reno_sqft > 0:
        spr = DIVISION_COSTS["21_fire_suppression"]["wet_sprinkler"]
        line_items.append(CostLineItem(
            division="21", category="Fire Suppression", description="Wet sprinkler system",
            quantity=reno_sqft, unit="SF",
            unit_cost=spr["total"] * combined_factor,
            total_cost=reno_sqft * spr["total"] * combined_factor,
            labor_cost=reno_sqft * spr["labor"] * combined_factor,
            material_cost=reno_sqft * spr["material"] * combined_factor,
            source="rsmeans",
        ))

    # Fuel canopy
    if scope.get("fuel_canopy"):
        fc = DIVISION_COSTS["32_exterior"]["fuel_canopy"]
        line_items.append(CostLineItem(
            division="32", category="Site Work", description="Fuel canopy",
            quantity=1, unit="EA",
            unit_cost=fc["total"] * combined_factor,
            total_cost=fc["total"] * combined_factor,
            labor_cost=fc["labor"] * combined_factor,
            material_cost=fc["material"] * combined_factor,
            source="rsmeans",
        ))

    # Parking
    parking = scope.get("parking_spaces", 0)
    if parking > 0:
        paving = DIVISION_COSTS["32_exterior"]["asphalt_paving"]
        park_sqft = parking * 180  # ~180 SF per space with drive aisle
        line_items.append(CostLineItem(
            division="32", category="Site Work", description=f"Parking lot ({parking} spaces)",
            quantity=park_sqft, unit="SF",
            unit_cost=paving["total"] * combined_factor,
            total_cost=park_sqft * paving["total"] * combined_factor,
            labor_cost=park_sqft * paving["labor"] * combined_factor,
            material_cost=park_sqft * paving["material"] * combined_factor,
            source="rsmeans",
        ))

    # ── Assemble Estimate ────────────────────────────────
    bottom_up_total = sum(li.total_cost for li in line_items)
    sqft_total = total_sqft * adjusted_per_sqft if total_sqft > 0 else 0

    # Use the higher of bottom-up vs sqft method (conservative)
    hard_cost = max(bottom_up_total, sqft_total)

    # If bottom-up is much less than sqft estimate, add "unallocated" line
    if bottom_up_total > 0 and sqft_total > bottom_up_total * 1.3:
        gap = sqft_total - bottom_up_total
        line_items.append(CostLineItem(
            division="01", category="General", description="Unallocated (GC overhead, misc scope)",
            quantity=1, unit="LS", unit_cost=gap, total_cost=gap,
            labor_cost=gap * 0.4, material_cost=gap * 0.6,
            source="sqft_benchmark",
        ))
        hard_cost = sqft_total

    # Soft costs
    arch_fee = hard_cost * 0.07
    eng_fee = hard_cost * 0.04
    permits = hard_cost * 0.025
    inspections = hard_cost * 0.01
    insurance = hard_cost * 0.015
    legal = 5000
    soft_total = arch_fee + eng_fee + permits + inspections + insurance + legal

    # Contingency
    design_contingency = hard_cost * 0.10
    construction_contingency = hard_cost * 0.10
    contingency = design_contingency + construction_contingency

    total = hard_cost + soft_total + contingency
    cost_sqft = total / total_sqft if total_sqft > 0 else 0

    estimate = CostEstimate(
        project_name=scope.get("project_name", ""),
        estimate_date=__import__("datetime").datetime.now().strftime("%Y-%m-%d"),
        location=city or scope.get("address", ""),
        state=state,
        line_items=line_items,
        hard_cost_subtotal=round(hard_cost, 2),
        architectural_fees=round(arch_fee, 2),
        engineering_fees=round(eng_fee, 2),
        permits_fees=round(permits, 2),
        inspections=round(inspections, 2),
        insurance=round(insurance, 2),
        legal=round(legal, 2),
        soft_cost_subtotal=round(soft_total, 2),
        design_contingency_pct=0.10,
        construction_contingency_pct=0.10,
        contingency_total=round(contingency, 2),
        total_project_cost=round(total, 2),
        cost_per_sqft=round(cost_sqft, 2),
        total_sqft=total_sqft,
        confidence="moderate",
        basis="schematic",
    )

    return estimate.to_dict()


def get_location_factor(state: str, city: str = "") -> float:
    """Get combined location cost factor."""
    base = LOCATION_FACTORS.get(state, 1.00)
    city_adj = CITY_FACTORS.get(city, 1.00)
    return round(base * city_adj, 4)
