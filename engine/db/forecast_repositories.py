"""
engine.db.forecast_repositories — Forecaster Repositories
============================================================
Phase 4: Model registry and prediction audit trail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, and_, func
from sqlalchemy.orm import Session

from engine.db.models import ModelRegistryRow, PredictionLogRow


# ═══════════════════════════════════════════════════════════════
# MODEL REGISTRY
# ═══════════════════════════════════════════════════════════════

class ModelRegistryRepo:
    """Tracks trained model versions with metrics and champion status."""

    def __init__(self, session: Session):
        self.session = session

    def register(
        self, model_name: str, model_type: str,
        metrics: Dict[str, Any], parameters: Dict[str, Any],
        training_data_range: str = "",
        artifact_path: str = "",
    ) -> Dict:
        """Register a new model version (auto-increments version)."""
        current_max = self.session.execute(
            select(func.max(ModelRegistryRow.version))
            .where(ModelRegistryRow.model_name == model_name)
        ).scalar_one_or_none() or 0

        row = ModelRegistryRow(
            model_name=model_name,
            version=current_max + 1,
            model_type=model_type,
            training_data_range=training_data_range,
            metrics=metrics,
            parameters=parameters,
            artifact_path=artifact_path,
            is_champion=False,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def promote(self, model_name: str, version: int, promoted_by: str = "") -> bool:
        """Promote a version to champion (demotes all others)."""
        # Demote all current champions for this model
        self.session.execute(
            update(ModelRegistryRow)
            .where(and_(
                ModelRegistryRow.model_name == model_name,
                ModelRegistryRow.is_champion == True,
            ))
            .values(is_champion=False)
        )
        # Promote the target version
        result = self.session.execute(
            update(ModelRegistryRow)
            .where(and_(
                ModelRegistryRow.model_name == model_name,
                ModelRegistryRow.version == version,
            ))
            .values(
                is_champion=True,
                promoted_at=datetime.now(timezone.utc),
                promoted_by=promoted_by,
            )
        )
        self.session.flush()
        return result.rowcount > 0

    def get_champion(self, model_name: str) -> Optional[Dict]:
        """Get the current champion version for a model."""
        row = self.session.execute(
            select(ModelRegistryRow).where(and_(
                ModelRegistryRow.model_name == model_name,
                ModelRegistryRow.is_champion == True,
            ))
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def get_version(self, model_name: str, version: int) -> Optional[Dict]:
        row = self.session.execute(
            select(ModelRegistryRow).where(and_(
                ModelRegistryRow.model_name == model_name,
                ModelRegistryRow.version == version,
            ))
        ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def list_versions(self, model_name: str) -> List[Dict]:
        rows = self.session.execute(
            select(ModelRegistryRow)
            .where(ModelRegistryRow.model_name == model_name)
            .order_by(ModelRegistryRow.version.desc())
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def list_models(self) -> List[Dict]:
        """List all distinct model names with their champion version."""
        rows = self.session.execute(
            select(ModelRegistryRow)
            .order_by(ModelRegistryRow.model_name, ModelRegistryRow.version.desc())
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: ModelRegistryRow) -> Dict:
        return {
            "id": row.id, "model_name": row.model_name,
            "version": row.version, "model_type": row.model_type,
            "training_data_range": row.training_data_range,
            "metrics": row.metrics, "parameters": row.parameters,
            "artifact_path": row.artifact_path,
            "is_champion": row.is_champion,
            "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
            "promoted_by": row.promoted_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


# ═══════════════════════════════════════════════════════════════
# PREDICTION LOG
# ═══════════════════════════════════════════════════════════════

class PredictionLogRepo:
    """Audit trail for every prediction made."""

    def __init__(self, session: Session):
        self.session = session

    def log(
        self, workspace_id: str, model_name: str, model_version: int,
        inputs: Dict, features: Dict, predictions: Dict,
        confidence: float, execution_ms: int = 0, user_id: str = "",
    ) -> int:
        row = PredictionLogRow(
            workspace_id=workspace_id,
            model_name=model_name,
            model_version=model_version,
            inputs=inputs,
            features=features,
            predictions=predictions,
            confidence=confidence,
            execution_ms=execution_ms,
            user_id=user_id,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def get(self, prediction_id: int) -> Optional[Dict]:
        row = self.session.get(PredictionLogRow, prediction_id)
        return self._to_dict(row) if row else None

    def list_recent(
        self, workspace_id: str, limit: int = 50,
    ) -> List[Dict]:
        rows = self.session.execute(
            select(PredictionLogRow)
            .where(PredictionLogRow.workspace_id == workspace_id)
            .order_by(PredictionLogRow.id.desc())
            .limit(limit)
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: PredictionLogRow) -> Dict:
        return {
            "id": row.id, "workspace_id": row.workspace_id,
            "model_name": row.model_name, "model_version": row.model_version,
            "inputs": row.inputs, "features": row.features,
            "predictions": row.predictions,
            "confidence": row.confidence, "execution_ms": row.execution_ms,
            "user_id": row.user_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
