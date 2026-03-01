"""
engine.db.egm_repositories — EGM Data Layer Repositories
==========================================================
Phase 3: Repositories for all EGM-related tables.

Handles the full data lifecycle:
  - Data sources (registry of state gaming boards)
  - EGM locations (venues with terminals)
  - Monthly performance (the core analytical dataset)
  - Ingest runs (job tracking)
  - Ingest errors (per-row error logging)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    select, update, delete, and_, or_, func, text, case, literal_column,
)
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session

from engine.db.models import (
    DataSourceRow, EGMLocationRow, EGMMonthlyPerformanceRow,
    IngestRunRow, IngestErrorRow,
)


# ═══════════════════════════════════════════════════════════════
# DATA SOURCE REPO
# ═══════════════════════════════════════════════════════════════

class DataSourceRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, name: str, source_type: str, **kwargs) -> Dict:
        row = DataSourceRow(name=name, source_type=source_type, **kwargs)
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get(self, source_id: int) -> Optional[Dict]:
        row = self.session.get(DataSourceRow, source_id)
        return self._to_dict(row) if row else None

    def get_by_name(self, name: str) -> Optional[Dict]:
        row = self.session.execute(
            select(DataSourceRow).where(DataSourceRow.name == name)
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def list_all(self, enabled_only: bool = False) -> List[Dict]:
        stmt = select(DataSourceRow)
        if enabled_only:
            stmt = stmt.where(DataSourceRow.enabled == True)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_dict(r) for r in rows]

    def update_last_synced(self, source_id: int, synced_at: Optional[datetime] = None):
        ts = synced_at or datetime.now(timezone.utc)
        self.session.execute(
            update(DataSourceRow)
            .where(DataSourceRow.id == source_id)
            .values(last_synced_at=ts)
        )
        self.session.flush()

    def _to_dict(self, row: DataSourceRow) -> Dict:
        return {
            "id": row.id, "name": row.name, "source_type": row.source_type,
            "url": row.url, "format": row.format, "frequency": row.frequency,
            "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            "enabled": row.enabled,
        }


# ═══════════════════════════════════════════════════════════════
# EGM LOCATION REPO
# ═══════════════════════════════════════════════════════════════

class EGMLocationRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, data_source_id: int, source_location_id: str,
               fields: Dict[str, Any]) -> Dict:
        """Upsert a location by (data_source_id, source_location_id)."""
        existing = self.session.execute(
            select(EGMLocationRow).where(and_(
                EGMLocationRow.data_source_id == data_source_id,
                EGMLocationRow.source_location_id == source_location_id,
            ))
        ).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if existing:
            for k, v in fields.items():
                if hasattr(existing, k) and k not in ("id", "data_source_id",
                                                       "source_location_id"):
                    setattr(existing, k, v)
            existing.last_seen_date = now
            self.session.flush()
            return self._to_dict(existing)
        else:
            row = EGMLocationRow(
                data_source_id=data_source_id,
                source_location_id=source_location_id,
                first_seen_date=now,
                last_seen_date=now,
                **{k: v for k, v in fields.items()
                   if hasattr(EGMLocationRow, k)
                   and k not in ("id", "data_source_id", "source_location_id",
                                  "first_seen_date", "last_seen_date")},
            )
            self.session.add(row)
            self.session.flush()
            return self._to_dict(row)

    def get(self, location_id: int) -> Optional[Dict]:
        row = self.session.get(EGMLocationRow, location_id)
        return self._to_dict(row) if row else None

    def find(self, data_source_id: int, source_location_id: str) -> Optional[Dict]:
        row = self.session.execute(
            select(EGMLocationRow).where(and_(
                EGMLocationRow.data_source_id == data_source_id,
                EGMLocationRow.source_location_id == source_location_id,
            ))
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def search(
        self, state: Optional[str] = None, venue_type: Optional[str] = None,
        municipality: Optional[str] = None, active_only: bool = True,
        limit: int = 100, offset: int = 0,
    ) -> List[Dict]:
        stmt = select(EGMLocationRow)
        if state:
            stmt = stmt.where(EGMLocationRow.state == state)
        if venue_type:
            stmt = stmt.where(EGMLocationRow.venue_type == venue_type)
        if municipality:
            stmt = stmt.where(EGMLocationRow.municipality.ilike(f"%{municipality}%"))
        if active_only:
            stmt = stmt.where(EGMLocationRow.is_active == True)
        stmt = stmt.order_by(EGMLocationRow.name).limit(limit).offset(offset)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_dict(r) for r in rows]

    def count(self, state: Optional[str] = None, active_only: bool = True) -> int:
        stmt = select(func.count(EGMLocationRow.id))
        if state:
            stmt = stmt.where(EGMLocationRow.state == state)
        if active_only:
            stmt = stmt.where(EGMLocationRow.is_active == True)
        return self.session.execute(stmt).scalar_one()

    def count_by_venue_type(self, state: Optional[str] = None) -> List[Dict]:
        stmt = (
            select(
                EGMLocationRow.venue_type,
                func.count(EGMLocationRow.id).label("count"),
            )
            .where(EGMLocationRow.is_active == True)
            .group_by(EGMLocationRow.venue_type)
        )
        if state:
            stmt = stmt.where(EGMLocationRow.state == state)
        rows = self.session.execute(stmt).all()
        return [{"venue_type": r[0], "count": r[1]} for r in rows]

    def _to_dict(self, row: EGMLocationRow) -> Dict:
        return {
            "id": row.id, "data_source_id": row.data_source_id,
            "source_location_id": row.source_location_id,
            "name": row.name, "address": row.address,
            "municipality": row.municipality, "county": row.county,
            "state": row.state, "venue_type": row.venue_type,
            "lat": row.lat, "lng": row.lng,
            "license_number": row.license_number,
            "terminal_operator": row.terminal_operator,
            "attributes": row.attributes or {},
            "first_seen_date": row.first_seen_date.isoformat() if row.first_seen_date else None,
            "last_seen_date": row.last_seen_date.isoformat() if row.last_seen_date else None,
            "is_active": row.is_active,
        }


# ═══════════════════════════════════════════════════════════════
# EGM MONTHLY PERFORMANCE REPO
# ═══════════════════════════════════════════════════════════════

class EGMPerformanceRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, location_id: int, data_source_id: int,
               report_month: datetime, fields: Dict[str, Any]) -> Dict:
        """Upsert on (location_id, report_month). Idempotent."""
        existing = self.session.execute(
            select(EGMMonthlyPerformanceRow).where(and_(
                EGMMonthlyPerformanceRow.location_id == location_id,
                EGMMonthlyPerformanceRow.report_month == report_month,
            ))
        ).scalar_one_or_none()

        if existing:
            for k, v in fields.items():
                if hasattr(existing, k) and k not in ("id", "location_id",
                                                       "data_source_id", "report_month"):
                    setattr(existing, k, v)
            self.session.flush()
            return self._to_dict(existing), False  # (dict, is_new)
        else:
            row = EGMMonthlyPerformanceRow(
                location_id=location_id,
                data_source_id=data_source_id,
                report_month=report_month,
                **{k: v for k, v in fields.items()
                   if hasattr(EGMMonthlyPerformanceRow, k)
                   and k not in ("id", "location_id", "data_source_id", "report_month")},
            )
            self.session.add(row)
            self.session.flush()
            return self._to_dict(row), True

    def get_history(
        self, location_id: int, limit: int = 120,
    ) -> List[Dict]:
        """Get performance history for a location, newest first."""
        rows = self.session.execute(
            select(EGMMonthlyPerformanceRow)
            .where(EGMMonthlyPerformanceRow.location_id == location_id)
            .order_by(EGMMonthlyPerformanceRow.report_month.desc())
            .limit(limit)
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def get_month(
        self, report_month: datetime,
        state: Optional[str] = None,
        venue_type: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict]:
        """Get all performance records for a specific month."""
        stmt = (
            select(EGMMonthlyPerformanceRow, EGMLocationRow)
            .join(EGMLocationRow, EGMMonthlyPerformanceRow.location_id == EGMLocationRow.id)
            .where(EGMMonthlyPerformanceRow.report_month == report_month)
        )
        if state:
            stmt = stmt.where(EGMLocationRow.state == state)
        if venue_type:
            stmt = stmt.where(EGMLocationRow.venue_type == venue_type)
        stmt = stmt.limit(limit)
        results = self.session.execute(stmt).all()
        return [
            {**self._to_dict(perf), "location_name": loc.name,
             "venue_type": loc.venue_type, "state": loc.state}
            for perf, loc in results
        ]

    def aggregate_by_state(self, report_month: datetime) -> List[Dict]:
        """Aggregate performance by state for a given month."""
        stmt = (
            select(
                EGMLocationRow.state,
                func.count(EGMMonthlyPerformanceRow.id).label("location_count"),
                func.sum(EGMMonthlyPerformanceRow.terminal_count).label("total_terminals"),
                func.sum(EGMMonthlyPerformanceRow.coin_in).label("total_coin_in"),
                func.sum(EGMMonthlyPerformanceRow.net_win).label("total_net_win"),
                func.avg(EGMMonthlyPerformanceRow.hold_pct).label("avg_hold_pct"),
                func.sum(EGMMonthlyPerformanceRow.tax_amount).label("total_tax"),
            )
            .join(EGMLocationRow, EGMMonthlyPerformanceRow.location_id == EGMLocationRow.id)
            .where(EGMMonthlyPerformanceRow.report_month == report_month)
            .group_by(EGMLocationRow.state)
        )
        rows = self.session.execute(stmt).all()
        return [
            {
                "state": r[0], "location_count": r[1],
                "total_terminals": r[2] or 0,
                "total_coin_in": float(r[3] or 0),
                "total_net_win": float(r[4] or 0),
                "avg_hold_pct": float(r[5] or 0),
                "total_tax": float(r[6] or 0),
            }
            for r in rows
        ]

    def aggregate_by_venue_type(
        self, report_month: datetime, state: Optional[str] = None,
    ) -> List[Dict]:
        stmt = (
            select(
                EGMLocationRow.venue_type,
                func.count(EGMMonthlyPerformanceRow.id).label("count"),
                func.sum(EGMMonthlyPerformanceRow.net_win).label("total_net_win"),
                func.avg(EGMMonthlyPerformanceRow.hold_pct).label("avg_hold_pct"),
                func.sum(EGMMonthlyPerformanceRow.terminal_count).label("total_terminals"),
            )
            .join(EGMLocationRow, EGMMonthlyPerformanceRow.location_id == EGMLocationRow.id)
            .where(EGMMonthlyPerformanceRow.report_month == report_month)
            .group_by(EGMLocationRow.venue_type)
        )
        if state:
            stmt = stmt.where(EGMLocationRow.state == state)
        rows = self.session.execute(stmt).all()
        return [
            {
                "venue_type": r[0], "count": r[1],
                "total_net_win": float(r[2] or 0),
                "avg_hold_pct": float(r[3] or 0),
                "total_terminals": r[4] or 0,
            }
            for r in rows
        ]

    def available_months(self, data_source_id: Optional[int] = None) -> List[str]:
        """List all months that have data, newest first."""
        stmt = (
            select(EGMMonthlyPerformanceRow.report_month)
            .distinct()
            .order_by(EGMMonthlyPerformanceRow.report_month.desc())
        )
        if data_source_id:
            stmt = stmt.where(
                EGMMonthlyPerformanceRow.data_source_id == data_source_id
            )
        rows = self.session.execute(stmt).scalars().all()
        return [r.strftime("%Y-%m") if hasattr(r, 'strftime') else str(r) for r in rows]

    def _to_dict(self, row: EGMMonthlyPerformanceRow) -> Dict:
        return {
            "id": row.id, "location_id": row.location_id,
            "data_source_id": row.data_source_id,
            "report_month": row.report_month.strftime("%Y-%m") if hasattr(row.report_month, 'strftime') else str(row.report_month),
            "terminal_count": row.terminal_count,
            "coin_in": float(row.coin_in or 0),
            "coin_out": float(row.coin_out or 0),
            "net_win": float(row.net_win or 0),
            "hold_pct": float(row.hold_pct or 0),
            "tax_amount": float(row.tax_amount or 0),
        }


# ═══════════════════════════════════════════════════════════════
# INGEST RUN REPO
# ═══════════════════════════════════════════════════════════════

class IngestRunRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self, data_source_id: int, run_type: str = "manual",
        workspace_id: str = "", triggered_by: str = "",
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> Dict:
        row = IngestRunRow(
            data_source_id=data_source_id,
            run_type=run_type,
            workspace_id=workspace_id,
            triggered_by=triggered_by,
            period_start=period_start,
            period_end=period_end,
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def start(self, run_id: int):
        self.session.execute(
            update(IngestRunRow)
            .where(IngestRunRow.id == run_id)
            .values(status="running", started_at=datetime.now(timezone.utc))
        )
        self.session.flush()

    def complete(
        self, run_id: int,
        rows_processed: int = 0, rows_inserted: int = 0,
        rows_updated: int = 0, rows_errored: int = 0,
    ):
        self.session.execute(
            update(IngestRunRow)
            .where(IngestRunRow.id == run_id)
            .values(
                status="completed",
                completed_at=datetime.now(timezone.utc),
                rows_processed=rows_processed,
                rows_inserted=rows_inserted,
                rows_updated=rows_updated,
                rows_errored=rows_errored,
            )
        )
        self.session.flush()

    def fail(self, run_id: int, rows_processed: int = 0, rows_errored: int = 0):
        self.session.execute(
            update(IngestRunRow)
            .where(IngestRunRow.id == run_id)
            .values(
                status="failed",
                completed_at=datetime.now(timezone.utc),
                rows_processed=rows_processed,
                rows_errored=rows_errored,
            )
        )
        self.session.flush()

    def get(self, run_id: int) -> Optional[Dict]:
        row = self.session.get(IngestRunRow, run_id)
        return self._to_dict(row) if row else None

    def list_runs(
        self, data_source_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict]:
        stmt = select(IngestRunRow).order_by(IngestRunRow.id.desc()).limit(limit)
        if data_source_id:
            stmt = stmt.where(IngestRunRow.data_source_id == data_source_id)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: IngestRunRow) -> Dict:
        return {
            "id": row.id, "data_source_id": row.data_source_id,
            "workspace_id": row.workspace_id,
            "run_type": row.run_type, "status": row.status,
            "period_start": row.period_start.isoformat() if row.period_start else None,
            "period_end": row.period_end.isoformat() if row.period_end else None,
            "rows_processed": row.rows_processed, "rows_inserted": row.rows_inserted,
            "rows_updated": row.rows_updated, "rows_errored": row.rows_errored,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "triggered_by": row.triggered_by,
        }


# ═══════════════════════════════════════════════════════════════
# INGEST ERROR REPO
# ═══════════════════════════════════════════════════════════════

class IngestErrorRepo:
    def __init__(self, session: Session):
        self.session = session

    def log_error(
        self, ingest_run_id: int, row_num: int,
        error_type: str, detail: str,
        source_column: str = "", raw_row: Optional[Dict] = None,
    ) -> int:
        row = IngestErrorRow(
            ingest_run_id=ingest_run_id,
            row_num=row_num,
            source_column=source_column,
            error_type=error_type,
            detail=detail,
            raw_row=raw_row,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_errors(self, ingest_run_id: int, limit: int = 100) -> List[Dict]:
        rows = self.session.execute(
            select(IngestErrorRow)
            .where(IngestErrorRow.ingest_run_id == ingest_run_id)
            .order_by(IngestErrorRow.row_num)
            .limit(limit)
        ).scalars().all()
        return [
            {
                "id": r.id, "ingest_run_id": r.ingest_run_id,
                "row_num": r.row_num, "source_column": r.source_column,
                "error_type": r.error_type, "detail": r.detail,
                "raw_row": r.raw_row,
            }
            for r in rows
        ]

    def count(self, ingest_run_id: int) -> int:
        return self.session.execute(
            select(func.count(IngestErrorRow.id))
            .where(IngestErrorRow.ingest_run_id == ingest_run_id)
        ).scalar_one()
