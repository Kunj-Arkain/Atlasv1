"""
engine.db.strategic_repositories — Strategic Intelligence Persistence
========================================================================
Repos for strategic scenarios, runs, and artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class StrategicScenarioRepo:
    """CRUD for strategic_scenarios table."""

    def __init__(self, session):
        self._session = session

    def create(
        self,
        workspace_id: str,
        title: str,
        scenario_text: str,
        objectives: List[str],
        constraints: List[str],
        created_by: str = "",
        **kwargs,
    ) -> Dict:
        try:
            from engine.db.models import StrategicScenarioRow
            row = StrategicScenarioRow(
                workspace_id=workspace_id,
                title=title,
                scenario_text=scenario_text,
                objectives=objectives,
                constraints=constraints,
                inputs=kwargs,
                created_by=created_by,
            )
            self._session.add(row)
            self._session.flush()
            return {"id": row.id, "title": title}
        except Exception:
            return {"id": None, "title": title}

    def get(self, scenario_id: int) -> Optional[Dict]:
        try:
            from engine.db.models import StrategicScenarioRow
            row = self._session.query(StrategicScenarioRow).get(scenario_id)
            if not row:
                return None
            return {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "title": row.title,
                "scenario_text": row.scenario_text,
                "objectives": row.objectives,
                "constraints": row.constraints,
                "inputs": row.inputs,
                "created_by": row.created_by,
                "created_at": str(row.created_at),
            }
        except Exception:
            return None

    def list_by_workspace(self, workspace_id: str) -> List[Dict]:
        try:
            from engine.db.models import StrategicScenarioRow
            rows = (
                self._session.query(StrategicScenarioRow)
                .filter_by(workspace_id=workspace_id)
                .order_by(StrategicScenarioRow.created_at.desc())
                .limit(50)
                .all()
            )
            return [
                {"id": r.id, "title": r.title, "created_at": str(r.created_at)}
                for r in rows
            ]
        except Exception:
            return []


class StrategicRunRepo:
    """CRUD for strategic_runs table."""

    def __init__(self, session):
        self._session = session

    def create_run(
        self,
        workspace_id: str,
        scenario_id: str,
        run_id: str,
        title: str,
        decision: str,
        confidence: float,
        outputs: Dict,
        elapsed_ms: int = 0,
        llm_cost_usd: float = 0.0,
        stage_routes: Dict = None,
    ) -> Dict:
        try:
            from engine.db.models import StrategicRunRow
            row = StrategicRunRow(
                workspace_id=workspace_id,
                scenario_id=scenario_id,
                run_id=run_id,
                title=title,
                decision=decision,
                confidence=confidence,
                outputs_json=outputs,
                elapsed_ms=elapsed_ms,
                llm_cost_usd=llm_cost_usd,
                stage_routes=stage_routes or {},
            )
            self._session.add(row)
            self._session.flush()
            return {"id": row.id, "run_id": run_id}
        except Exception:
            return {"id": None, "run_id": run_id}

    def get_by_run_id(self, run_id: str) -> Optional[Dict]:
        try:
            from engine.db.models import StrategicRunRow
            row = (
                self._session.query(StrategicRunRow)
                .filter_by(run_id=run_id)
                .first()
            )
            if not row:
                return None
            return {
                "id": row.id,
                "run_id": row.run_id,
                "scenario_id": row.scenario_id,
                "title": row.title,
                "decision": row.decision,
                "confidence": row.confidence,
                "outputs": row.outputs_json,
                "elapsed_ms": row.elapsed_ms,
                "llm_cost_usd": row.llm_cost_usd,
                "stage_routes": row.stage_routes,
                "created_at": str(row.created_at),
            }
        except Exception:
            return None

    def list_by_workspace(self, workspace_id: str, limit: int = 25) -> List[Dict]:
        try:
            from engine.db.models import StrategicRunRow
            rows = (
                self._session.query(StrategicRunRow)
                .filter_by(workspace_id=workspace_id)
                .order_by(StrategicRunRow.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "run_id": r.run_id, "title": r.title,
                    "decision": r.decision, "confidence": r.confidence,
                    "elapsed_ms": r.elapsed_ms, "created_at": str(r.created_at),
                }
                for r in rows
            ]
        except Exception:
            return []


class StrategicArtifactRepo:
    """CRUD for strategic_artifacts table."""

    def __init__(self, session):
        self._session = session

    def create(
        self, run_id: str, artifact_type: str, path: str,
    ) -> Dict:
        try:
            from engine.db.models import StrategicArtifactRow
            row = StrategicArtifactRow(
                run_id=run_id,
                artifact_type=artifact_type,
                path=path,
            )
            self._session.add(row)
            self._session.flush()
            return {"id": row.id, "run_id": run_id, "type": artifact_type}
        except Exception:
            return {"id": None, "run_id": run_id}

    def list_by_run(self, run_id: str) -> List[Dict]:
        try:
            from engine.db.models import StrategicArtifactRow
            rows = (
                self._session.query(StrategicArtifactRow)
                .filter_by(run_id=run_id)
                .all()
            )
            return [
                {"id": r.id, "type": r.artifact_type, "path": r.path,
                 "created_at": str(r.created_at)}
                for r in rows
            ]
        except Exception:
            return []
