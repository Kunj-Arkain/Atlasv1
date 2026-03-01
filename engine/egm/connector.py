"""
engine.egm.connector — Data Source Connectors
================================================
Phase 3: Connectors for fetching and parsing public EGM data.

Each state gaming board publishes data in different formats.
Connectors normalize everything into a common ParsedRow format
that the ingestion pipeline can load uniformly.

Connectors do NOT touch the database — they only fetch and parse.
The pipeline.py module handles persistence.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple

from engine.egm.classifier import classify_venue, extract_operator

logger = logging.getLogger("engine.egm.connector")


# ═══════════════════════════════════════════════════════════════
# COMMON DATA TYPES
# ═══════════════════════════════════════════════════════════════

@dataclass
class ParsedRow:
    """Canonical format for a single ingested performance record.

    Every connector produces these. The pipeline loads them.
    """
    source_location_id: str      # Original ID from state data
    name: str                    # Establishment name
    municipality: str = ""
    county: str = ""
    state: str = ""
    license_number: str = ""
    terminal_operator: str = ""
    venue_type: str = ""         # Classified by classifier.py
    report_month: Optional[datetime] = None  # First of month
    terminal_count: int = 0
    coin_in: float = 0.0
    coin_out: float = 0.0
    net_win: float = 0.0
    hold_pct: float = 0.0
    tax_amount: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)  # Original row for debugging


@dataclass
class ParseError:
    """Error encountered during parsing."""
    row_num: int
    column: str
    error_type: str
    detail: str
    raw_row: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    """Output from a connector parse operation."""
    rows: List[ParsedRow]
    errors: List[ParseError]
    report_month: Optional[datetime] = None
    source_name: str = ""
    raw_row_count: int = 0


# ═══════════════════════════════════════════════════════════════
# CONNECTOR INTERFACE
# ═══════════════════════════════════════════════════════════════

class DataSourceConnector(ABC):
    """Abstract base for all data source connectors.

    Subclasses implement:
      - parse_csv(content, report_month) → ParseResult
      - Optional: fetch(url) → bytes (default uses urllib)
    """

    source_name: str = ""
    source_type: str = ""
    state: str = ""
    data_format: str = "csv"

    @abstractmethod
    def parse_csv(
        self, content: str, report_month: datetime,
    ) -> ParseResult:
        """Parse raw CSV/text content into ParsedRows.

        Args:
            content: Raw text content of the data file
            report_month: The month this data represents

        Returns:
            ParseResult with rows and any errors
        """
        ...

    def parse_file(
        self, file_bytes: bytes, report_month: datetime,
        encoding: str = "utf-8",
    ) -> ParseResult:
        """Parse a file (bytes) into ParsedRows.

        Default implementation decodes to string and calls parse_csv.
        Override for binary formats (Excel, PDF).
        """
        content = file_bytes.decode(encoding, errors="replace")
        return self.parse_csv(content, report_month)


# ═══════════════════════════════════════════════════════════════
# ILLINOIS IGB CONNECTOR
# ═══════════════════════════════════════════════════════════════

class IllinoisIGBConnector(DataSourceConnector):
    """Parses Illinois Gaming Board monthly video gaming CSVs.

    IGB CSV columns (typical):
      Municipality, Establishment, License #, Terminal Operator,
      # of VGTs, Funds In, Funds Out, NTI, State Tax,
      Municipality Share

    Column names vary slightly by year — this connector handles
    common variations.
    """

    source_name = "illinois_igb"
    source_type = "state_gaming_board"
    state = "IL"
    data_format = "csv"

    # Column name mapping: IGB name → canonical field
    _COLUMN_MAP = {
        # Municipality
        "municipality": "municipality",
        "city": "municipality",
        # Establishment name
        "establishment": "name",
        "establishment name": "name",
        "location": "name",
        # License
        "license #": "license_number",
        "license number": "license_number",
        "license": "license_number",
        "lic #": "license_number",
        # Terminal operator
        "terminal operator": "terminal_operator",
        "operator": "terminal_operator",
        "route operator": "terminal_operator",
        # Terminal count
        "# of vgts": "terminal_count",
        "number of vgts": "terminal_count",
        "vgts": "terminal_count",
        "vgt count": "terminal_count",
        "terminals": "terminal_count",
        # Financial
        "funds in": "coin_in",
        "amount played": "coin_in",
        "coin in": "coin_in",
        "funds out": "coin_out",
        "amount won": "coin_out",
        "coin out": "coin_out",
        "nti": "net_win",
        "net terminal income": "net_win",
        "net win": "net_win",
        "state tax": "tax_state",
        "state share": "tax_state",
        "municipality share": "tax_municipal",
        "municipal share": "tax_municipal",
    }

    def parse_csv(
        self, content: str, report_month: datetime,
    ) -> ParseResult:
        rows: List[ParsedRow] = []
        errors: List[ParseError] = []

        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            errors.append(ParseError(
                row_num=0, column="", error_type="parse_error",
                detail="No header row found in CSV",
            ))
            return ParseResult(rows=rows, errors=errors,
                               report_month=report_month,
                               source_name=self.source_name)

        # Build column mapping from actual headers
        col_map = self._build_column_map(reader.fieldnames)

        raw_count = 0
        for row_num, raw_row in enumerate(reader, start=2):
            raw_count += 1
            try:
                parsed = self._parse_row(raw_row, col_map, report_month, row_num)
                if parsed:
                    rows.append(parsed)
            except Exception as e:
                errors.append(ParseError(
                    row_num=row_num, column="",
                    error_type="parse_error",
                    detail=str(e)[:500],
                    raw_row=dict(raw_row),
                ))

        return ParseResult(
            rows=rows, errors=errors,
            report_month=report_month,
            source_name=self.source_name,
            raw_row_count=raw_count,
        )

    def _build_column_map(self, fieldnames: List[str]) -> Dict[str, str]:
        """Map actual CSV headers to canonical field names."""
        col_map = {}
        for header in fieldnames:
            normalized = header.strip().lower()
            canonical = self._COLUMN_MAP.get(normalized)
            if canonical:
                col_map[header] = canonical
        return col_map

    def _parse_row(
        self, raw_row: Dict[str, str], col_map: Dict[str, str],
        report_month: datetime, row_num: int,
    ) -> Optional[ParsedRow]:
        """Parse a single CSV row into a ParsedRow."""
        # Extract fields using column map
        fields: Dict[str, Any] = {}
        for csv_col, canonical in col_map.items():
            val = raw_row.get(csv_col, "").strip()
            if val:
                fields[canonical] = val

        # Must have a name
        name = fields.get("name", "")
        if not name:
            return None  # Skip empty rows

        # Build location ID from license or name+municipality
        license_num = fields.get("license_number", "")
        municipality = fields.get("municipality", "")
        source_location_id = license_num or f"{name}|{municipality}"

        # Parse numeric fields
        coin_in = self._parse_currency(fields.get("coin_in", "0"))
        coin_out = self._parse_currency(fields.get("coin_out", "0"))
        net_win = self._parse_currency(fields.get("net_win", "0"))
        tax_state = self._parse_currency(fields.get("tax_state", "0"))
        tax_municipal = self._parse_currency(fields.get("tax_municipal", "0"))
        terminal_count = self._parse_int(fields.get("terminal_count", "0"))

        # Compute hold percentage
        hold_pct = 0.0
        if coin_in > 0:
            hold_pct = round(net_win / coin_in, 6)

        # Classify venue type
        venue_type = classify_venue(name)

        # Normalize operator
        raw_operator = fields.get("terminal_operator", "")
        operator = extract_operator(raw_operator)

        return ParsedRow(
            source_location_id=source_location_id,
            name=name,
            municipality=municipality,
            state="IL",
            license_number=license_num,
            terminal_operator=operator,
            venue_type=venue_type,
            report_month=report_month,
            terminal_count=terminal_count,
            coin_in=round(coin_in, 2),
            coin_out=round(coin_out, 2),
            net_win=round(net_win, 2),
            hold_pct=hold_pct,
            tax_amount=round(tax_state + tax_municipal, 2),
            raw=dict(raw_row),
        )

    @staticmethod
    def _parse_currency(value: str) -> float:
        """Parse a currency string like '$1,234.56' or '(1,234.56)' to float."""
        if not value:
            return 0.0
        # Remove $, commas, spaces
        cleaned = re.sub(r'[$,\s]', '', value.strip())
        # Handle parentheses for negatives: (123.45) → -123.45
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = '-' + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_int(value: str) -> int:
        """Parse an integer string, handling commas."""
        if not value:
            return 0
        cleaned = re.sub(r'[,\s]', '', value.strip())
        try:
            return int(float(cleaned))
        except ValueError:
            return 0


# ═══════════════════════════════════════════════════════════════
# CONNECTOR REGISTRY
# ═══════════════════════════════════════════════════════════════

_CONNECTORS: Dict[str, DataSourceConnector] = {
    "illinois_igb": IllinoisIGBConnector(),
}


def get_connector(source_name: str) -> Optional[DataSourceConnector]:
    """Get a connector by source name."""
    return _CONNECTORS.get(source_name)


def list_connectors() -> List[str]:
    """List all registered connector names."""
    return list(_CONNECTORS.keys())
