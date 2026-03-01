"""
engine.egm.classifier — Venue Type Classifier
=================================================
Phase 3: Classifies EGM locations by venue type from
their establishment name. Pure heuristic, zero dependencies.

Classification hierarchy:
  1. Fraternal organizations (VFW, Legion, Moose, Elks, etc.)
  2. Truck stops / travel centers
  3. Bars / taverns / pubs
  4. Restaurants / grills / diners
  5. Gaming cafes / slot parlors
  6. Convenience stores / gas stations
  7. Casinos (for Nevada/Pennsylvania data)
  8. Other (manual review recommended)
"""

from __future__ import annotations

import re
from typing import Optional


# Keyword → venue_type mapping, checked in priority order
_RULES = [
    # Fraternal organizations — most distinctive, check first
    (
        ["VFW", "V.F.W.", "VETERANS OF FOREIGN",
         "AMERICAN LEGION", "AMVETS", "AM VETS",
         "MOOSE", "LOYAL ORDER",
         "ELKS", "B.P.O.E.",
         "KNIGHTS OF COLUMBUS", "K OF C", "KOC",
         "EAGLES", "FOE ", "F.O.E.",
         "LIONS CLUB", "KIWANIS",
         "MASONIC", "MASONS",
         "ODD FELLOWS", "IOOF",
         "POLISH NATIONAL", "ITALIAN AMERICAN",
         "IRISH AMERICAN", "GERMAN AMERICAN",
         "UKRAINIAN", "CROATIAN", "SERBIAN",
         "VETERANS", "MEMORIAL POST"],
        "fraternal",
    ),
    # Truck stops / travel centers
    (
        ["TRUCK STOP", "TRUCK PLAZA", "TRUCKSTOP",
         "TRAVEL CENTER", "TRAVEL PLAZA", "TRAVELCENTER",
         "FLYING J", "PILOT ", "LOVES ", "LOVE'S",
         "PETRO ", "FUEL STOP", "FUEL MART"],
        "truck_stop",
    ),
    # Bars / taverns / pubs
    (
        ["BAR ", " BAR", "TAVERN", "PUB ", " PUB",
         "TAP ", " TAP", "TAPROOM", "TAP ROOM",
         "SALOON", "LOUNGE", "COCKTAIL",
         "BREWERY", "BREW PUB", "BREWPUB",
         "CANTINA", "SPEAKEASY",
         "SPORTS BAR", "WINE BAR",
         "ALE HOUSE", "ALEHOUSE",
         "TAPHOUSE", "TAP HOUSE",
         "INN ", " INN"],
        "bar",
    ),
    # Gaming cafes / slot parlors — BEFORE restaurant (CAFE overlap)
    (
        ["GAMING", "SLOTS", "SLOT ",
         "AMUSEMENT", "ARCADE", "GAME ROOM",
         "VIDEO POKER", "VIDEO GAMING",
         "LUCKY ", "JACKPOT", "GOLD RUSH",
         "DOTTY'S", "DOTTYS",
         "STELLA'S", "SHELBY'S",
         "PT'S", "PTS ", "SIERRA GOLD",
         "GAMES ", " GAMES"],
        "gaming_cafe",
    ),
    # Restaurants / grills / diners
    (
        ["RESTAURANT", "RISTORANTE",
         "GRILL ", " GRILL", "GRILLE",
         "DINER", "CAFE ", " CAFE", "CAFÉ",
         "KITCHEN", "EATERY", "BISTRO",
         "PIZZERIA", "PIZZA", "TAQUERIA",
         "BBQ", "BARBECUE", "BAR-B-QUE",
         "STEAKHOUSE", "STEAK HOUSE",
         "SEAFOOD", "SUSHI", "THAI",
         "CHINESE", "MEXICAN", "ITALIAN",
         "BUFFET", "CATERING",
         "DELI ", " DELI", "SUB ", "SANDWICH",
         "WING ", " WINGS", "BURGER",
         "WAFFLE", "PANCAKE",
         "BAKERY", "DONUT", "DOUGHNUT"],
        "restaurant",
    ),
    # Gas stations / convenience stores
    (
        ["GAS STATION", "GAS MART",
         "CONVENIENCE", "C-STORE",
         "SHELL ", "BP ", "MARATHON",
         "MOBIL ", "CITGO", "CASEY'S",
         "7-ELEVEN", "SEVEN ELEVEN",
         "QUICK STOP", "QUIK STOP",
         "MINI MART", "MINI-MART"],
        "gas_station",
    ),
    # Casinos (for NV/PA/CO data)
    (
        ["CASINO", "RESORT ", "HOTEL ",
         "MGM", "WYNN", "BELLAGIO",
         "HARRAH", "CAESARS", "TROPICANA",
         "RIVERS", "PARX", "SUGARHOUSE",
         "MOUNT AIRY", "MOHEGAN", "WIND CREEK"],
        "casino",
    ),
]

# Pre-compile for performance
_COMPILED_RULES = [
    (
        re.compile(
            "|".join(re.escape(kw) for kw in keywords),
            re.IGNORECASE,
        ),
        venue_type,
    )
    for keywords, venue_type in _RULES
]


def classify_venue(name: str) -> str:
    """Classify a venue name into a venue type.

    Args:
        name: Establishment/venue name (e.g., "LUCKY DOG BAR & GRILL")

    Returns:
        venue_type string: 'bar', 'restaurant', 'truck_stop',
        'fraternal', 'gaming_cafe', 'gas_station', 'casino', or 'other'
    """
    if not name:
        return "other"

    upper = name.upper().strip()

    for pattern, venue_type in _COMPILED_RULES:
        if pattern.search(upper):
            return venue_type

    return "other"


def classify_venue_batch(names: list[str]) -> list[str]:
    """Classify multiple venue names."""
    return [classify_venue(n) for n in names]


# ═══════════════════════════════════════════════════════════════
# TERMINAL OPERATOR EXTRACTION (Illinois-specific)
# ═══════════════════════════════════════════════════════════════

_KNOWN_OPERATORS = {
    "ACCEL": "Accel Entertainment",
    "J&J": "J&J Ventures",
    "GOLD RUSH": "Gold Rush Amusements",
    "MIDWEST": "Midwest Electronics Gaming",
    "RICK'S": "Rick's Amusements",
    "STELLA'S PLACE": "Stella's Place",
    "ACTION GAMING": "Action Gaming",
    "FIRST MIDWEST": "First Midwest Gaming",
    "GRAND RIVER": "Grand River Jackpot",
    "INCREDIBLE": "Incredible Technologies",
    "ILLINOIS GAMING": "Illinois Gaming Systems",
    "LAREDO": "Laredo Hospitality",
}


def extract_operator(raw_operator: str) -> str:
    """Normalize terminal operator name.

    Args:
        raw_operator: Raw operator string from state data

    Returns:
        Normalized operator name
    """
    if not raw_operator:
        return ""

    upper = raw_operator.upper().strip()
    for key, canonical in _KNOWN_OPERATORS.items():
        if key in upper:
            return canonical

    # Return cleaned-up original if no match
    return raw_operator.strip().title()
