"""
engine.egm.pipeline — EGM Ingestion Pipeline
================================================
Phase 3: Orchestrates the full ingest cycle:
  1. Fetch raw data (CSV/Excel/PDF)
  2. Parse via source-specific connector
  3. Upsert locations + performance records
  4. Log errors, update ingest run stats
  5. Update data source last_synced_at

Fully idempotent — safe to re-run any month.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from engine.egm.connector import (
    DataSourceConnector, ParseResult, ParsedRow, ParseError,
    get_connector,
)
from engine.db.egm_repositories import (
    DataSourceRepo, EGMLocationRepo, EGMPerformanceRepo,
    IngestRunRepo, IngestErrorRepo,
)

logger = logging.getLogger("engine.egm.pipeline")


class IngestPipeline:
    """Orchestrates EGM data ingestion from raw content to DB.

    Usage:
        pipeline = IngestPipeline(session)
        result = pipeline.ingest(
            source_name="illinois_igb",
            content=csv_text,
            report_month=datetime(2024, 1, 1),
            triggered_by="admin",
        )
        print(f"Inserted {result['rows_inserted']}, "
              f"Updated {result['rows_updated']}, "
              f"Errors {result['rows_errored']}")
    """

    def __init__(self, session, workspace_id: str = ""):
        self._session = session
        self._workspace_id = workspace_id
        self._ds_repo = DataSourceRepo(session)
        self._loc_repo = EGMLocationRepo(session)
        self._perf_repo = EGMPerformanceRepo(session)
        self._run_repo = IngestRunRepo(session)
        self._err_repo = IngestErrorRepo(session)

    def ingest(
        self,
        source_name: str,
        content: str,
        report_month: datetime,
        triggered_by: str = "system",
        run_type: str = "manual",
    ) -> Dict:
        """Ingest data from raw content.

        Args:
            source_name: Connector name (e.g., "illinois_igb")
            content: Raw CSV/text content
            report_month: The month this data represents (first of month)
            triggered_by: User or system that triggered the ingest
            run_type: "manual", "scheduled", or "backfill"

        Returns:
            Dict with ingest statistics
        """
        # 1. Get or create data source
        source = self._ensure_data_source(source_name)
        source_id = source["id"]

        # 2. Create ingest run
        run = self._run_repo.create(
            data_source_id=source_id,
            run_type=run_type,
            workspace_id=self._workspace_id,
            triggered_by=triggered_by,
            period_start=report_month,
            period_end=report_month,
        )
        run_id = run["id"]
        self._run_repo.start(run_id)

        try:
            # 3. Parse via connector
            connector = get_connector(source_name)
            if not connector:
                raise ValueError(f"No connector for source: {source_name}")

            parse_result = connector.parse_csv(content, report_month)

            # 4. Load parsed rows
            stats = self._load_rows(source_id, run_id, parse_result)

            # 5. Log parse errors
            for err in parse_result.errors:
                self._err_repo.log_error(
                    ingest_run_id=run_id,
                    row_num=err.row_num,
                    error_type=err.error_type,
                    detail=err.detail,
                    source_column=err.column,
                    raw_row=err.raw_row,
                )

            # 6. Complete run
            total_errors = stats["errors"] + len(parse_result.errors)
            self._run_repo.complete(
                run_id,
                rows_processed=parse_result.raw_row_count,
                rows_inserted=stats["inserted"],
                rows_updated=stats["updated"],
                rows_errored=total_errors,
            )

            # 7. Update last synced
            self._ds_repo.update_last_synced(source_id)

            result = {
                "run_id": run_id,
                "source_name": source_name,
                "report_month": report_month.strftime("%Y-%m"),
                "status": "completed",
                "raw_rows": parse_result.raw_row_count,
                "rows_processed": len(parse_result.rows),
                "rows_inserted": stats["inserted"],
                "rows_updated": stats["updated"],
                "rows_errored": total_errors,
                "parse_errors": len(parse_result.errors),
                "load_errors": stats["errors"],
            }

            logger.info(
                f"Ingest completed: {source_name} {report_month.strftime('%Y-%m')} — "
                f"inserted={stats['inserted']}, updated={stats['updated']}, "
                f"errors={total_errors}"
            )

            return result

        except Exception as e:
            self._run_repo.fail(run_id)
            logger.exception(f"Ingest failed: {source_name} — {e}")
            return {
                "run_id": run_id,
                "source_name": source_name,
                "report_month": report_month.strftime("%Y-%m"),
                "status": "failed",
                "error": str(e),
            }

    def ingest_batch(
        self,
        source_name: str,
        content_by_month: Dict[datetime, str],
        triggered_by: str = "system",
    ) -> List[Dict]:
        """Ingest multiple months of data (backfill).

        Args:
            source_name: Connector name
            content_by_month: {report_month: csv_content}
            triggered_by: User or system

        Returns:
            List of ingest results, one per month
        """
        results = []
        for month in sorted(content_by_month.keys()):
            result = self.ingest(
                source_name=source_name,
                content=content_by_month[month],
                report_month=month,
                triggered_by=triggered_by,
                run_type="backfill",
            )
            results.append(result)
        return results

    def _ensure_data_source(self, source_name: str) -> Dict:
        """Get or create the data source entry."""
        existing = self._ds_repo.get_by_name(source_name)
        if existing:
            return existing

        connector = get_connector(source_name)
        return self._ds_repo.create(
            name=source_name,
            source_type=connector.source_type if connector else "unknown",
            url="",
            format=connector.data_format if connector else "csv",
            frequency="monthly",
        )

    def _load_rows(
        self, source_id: int, run_id: int, parse_result: ParseResult,
    ) -> Dict[str, int]:
        """Load parsed rows into egm_locations + egm_monthly_performance."""
        inserted = 0
        updated = 0
        errors = 0

        for i, row in enumerate(parse_result.rows):
            try:
                # Upsert location
                loc = self._loc_repo.upsert(
                    data_source_id=source_id,
                    source_location_id=row.source_location_id,
                    fields={
                        "name": row.name,
                        "municipality": row.municipality,
                        "county": row.county,
                        "state": row.state,
                        "venue_type": row.venue_type,
                        "license_number": row.license_number,
                        "terminal_operator": row.terminal_operator,
                        "is_active": True,
                    },
                )
                location_id = loc["id"]

                # Upsert performance
                if row.report_month:
                    _, is_new = self._perf_repo.upsert(
                        location_id=location_id,
                        data_source_id=source_id,
                        report_month=row.report_month,
                        fields={
                            "terminal_count": row.terminal_count,
                            "coin_in": row.coin_in,
                            "coin_out": row.coin_out,
                            "net_win": row.net_win,
                            "hold_pct": row.hold_pct,
                            "tax_amount": row.tax_amount,
                        },
                    )
                    if is_new:
                        inserted += 1
                    else:
                        updated += 1

            except Exception as e:
                errors += 1
                self._err_repo.log_error(
                    ingest_run_id=run_id,
                    row_num=i + 1,
                    error_type="load_error",
                    detail=str(e)[:500],
                    raw_row=row.raw if row.raw else {"name": row.name},
                )

        return {"inserted": inserted, "updated": updated, "errors": errors}
