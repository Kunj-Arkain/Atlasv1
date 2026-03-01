"""
engine.db.deal_repositories — Deal Pipeline Repositories
============================================================
Phase 6: Property templates and deal run persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, and_
from sqlalchemy.orm import Session

from engine.db.models import PropertyTemplateRow, DealRunRow


class PropertyTemplateRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self, workspace_id: str, name: str, property_type: str,
        defaults: Dict = None, scoring_weights: Dict = None,
    ) -> Dict:
        row = PropertyTemplateRow(
            workspace_id=workspace_id, name=name,
            property_type=property_type,
            defaults=defaults or {},
            scoring_weights=scoring_weights or {},
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get(self, template_id: int) -> Optional[Dict]:
        row = self.session.get(PropertyTemplateRow, template_id)
        return self._to_dict(row) if row else None

    def list_templates(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(PropertyTemplateRow)
            .where(and_(
                PropertyTemplateRow.workspace_id == workspace_id,
                PropertyTemplateRow.is_active == True,
            ))
            .order_by(PropertyTemplateRow.name)
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def get_by_type(self, workspace_id: str, property_type: str) -> Optional[Dict]:
        row = self.session.execute(
            select(PropertyTemplateRow).where(and_(
                PropertyTemplateRow.workspace_id == workspace_id,
                PropertyTemplateRow.property_type == property_type,
                PropertyTemplateRow.is_active == True,
            ))
        ).scalars().first()
        return self._to_dict(row) if row else None

    def _to_dict(self, row: PropertyTemplateRow) -> Dict:
        return {
            "id": row.id, "workspace_id": row.workspace_id,
            "name": row.name, "property_type": row.property_type,
            "defaults": row.defaults, "scoring_weights": row.scoring_weights,
            "is_active": row.is_active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


class DealRunRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self, workspace_id: str, deal_name: str, property_type: str,
        inputs: Dict, template_id: int = None, user_id: str = "",
    ) -> Dict:
        row = DealRunRow(
            workspace_id=workspace_id, deal_name=deal_name,
            property_type=property_type, template_id=template_id,
            inputs=inputs, status="pending", user_id=user_id,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get(self, deal_id: int) -> Optional[Dict]:
        row = self.session.get(DealRunRow, deal_id)
        return self._to_dict(row) if row else None

    def update_status(self, deal_id: int, status: str) -> bool:
        result = self.session.execute(
            update(DealRunRow).where(DealRunRow.id == deal_id)
            .values(status=status)
        )
        self.session.flush()
        return result.rowcount > 0

    def complete(
        self, deal_id: int, stage_results: Dict, scores: Dict,
        decision: str, rationale: str,
    ) -> bool:
        result = self.session.execute(
            update(DealRunRow).where(DealRunRow.id == deal_id)
            .values(
                status="completed",
                stage_results=stage_results,
                scores=scores,
                decision=decision,
                decision_rationale=rationale,
                completed_at=datetime.now(timezone.utc),
            )
        )
        self.session.flush()
        return result.rowcount > 0

    def fail(self, deal_id: int, error: str) -> bool:
        result = self.session.execute(
            update(DealRunRow).where(DealRunRow.id == deal_id)
            .values(
                status="failed",
                decision_rationale=f"Pipeline failed: {error}",
            )
        )
        self.session.flush()
        return result.rowcount > 0

    def list_runs(
        self, workspace_id: str, status: str = "", limit: int = 50,
    ) -> List[Dict]:
        q = select(DealRunRow).where(DealRunRow.workspace_id == workspace_id)
        if status:
            q = q.where(DealRunRow.status == status)
        rows = self.session.execute(
            q.order_by(DealRunRow.id.desc()).limit(limit)
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: DealRunRow) -> Dict:
        return {
            "id": row.id, "workspace_id": row.workspace_id,
            "deal_name": row.deal_name, "property_type": row.property_type,
            "template_id": row.template_id,
            "status": row.status,
            "inputs": row.inputs, "stage_results": row.stage_results,
            "scores": row.scores,
            "decision": row.decision,
            "decision_rationale": row.decision_rationale,
            "user_id": row.user_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
