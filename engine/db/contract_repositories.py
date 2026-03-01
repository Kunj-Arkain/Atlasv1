"""
engine.db.contract_repositories — Contract Engine Repositories
================================================================
Phase 5: Repositories for contract templates, overrides,
and simulation run audit trail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, and_, func
from sqlalchemy.orm import Session

from engine.db.models import ContractTemplateRow, SimulationRunRow


class ContractTemplateRepo:
    """CRUD for contract templates with versioning."""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self, workspace_id: str, name: str, agreement_type: str,
        terms: Dict, constraints: Optional[Dict] = None,
        acquisition_type: str = "cash",
        state_applicability: str = "",
        approval_required: bool = False,
        created_by: str = "",
    ) -> Dict:
        # Auto-version: find max version for this name
        current_max = self.session.execute(
            select(func.max(ContractTemplateRow.version))
            .where(and_(
                ContractTemplateRow.workspace_id == workspace_id,
                ContractTemplateRow.name == name,
            ))
        ).scalar_one_or_none() or 0

        row = ContractTemplateRow(
            workspace_id=workspace_id,
            name=name,
            version=current_max + 1,
            agreement_type=agreement_type,
            acquisition_type=acquisition_type,
            terms=terms,
            constraints=constraints or {},
            state_applicability=state_applicability,
            approval_required=approval_required,
            created_by=created_by,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get(self, template_id: int) -> Optional[Dict]:
        row = self.session.get(ContractTemplateRow, template_id)
        return self._to_dict(row) if row else None

    def get_latest(self, workspace_id: str, name: str) -> Optional[Dict]:
        row = self.session.execute(
            select(ContractTemplateRow)
            .where(and_(
                ContractTemplateRow.workspace_id == workspace_id,
                ContractTemplateRow.name == name,
                ContractTemplateRow.is_active == True,
            ))
            .order_by(ContractTemplateRow.version.desc())
        ).scalars().first()
        return self._to_dict(row) if row else None

    def list_templates(
        self, workspace_id: str, agreement_type: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict]:
        stmt = select(ContractTemplateRow).where(
            ContractTemplateRow.workspace_id == workspace_id
        )
        if agreement_type:
            stmt = stmt.where(
                ContractTemplateRow.agreement_type == agreement_type
            )
        if active_only:
            stmt = stmt.where(ContractTemplateRow.is_active == True)
        stmt = stmt.order_by(
            ContractTemplateRow.name, ContractTemplateRow.version.desc()
        )
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_dict(r) for r in rows]

    def deactivate(self, template_id: int):
        self.session.execute(
            update(ContractTemplateRow)
            .where(ContractTemplateRow.id == template_id)
            .values(is_active=False)
        )
        self.session.flush()

    def _to_dict(self, row: ContractTemplateRow) -> Dict:
        return {
            "id": row.id, "workspace_id": row.workspace_id,
            "name": row.name, "version": row.version,
            "agreement_type": row.agreement_type,
            "acquisition_type": row.acquisition_type,
            "terms": row.terms, "constraints": row.constraints,
            "state_applicability": row.state_applicability,
            "approval_required": row.approval_required,
            "is_active": row.is_active,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


class SimulationRunRepo:
    """Audit trail for Monte Carlo simulation runs."""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self, workspace_id: str, inputs: Dict,
        results: Dict, num_simulations: int = 10000,
        template_id: Optional[int] = None,
        overrides: Optional[Dict] = None,
        scenario_name: str = "",
        execution_ms: int = 0, user_id: str = "",
    ) -> Dict:
        row = SimulationRunRow(
            workspace_id=workspace_id,
            template_id=template_id,
            scenario_name=scenario_name,
            inputs=inputs,
            overrides=overrides or {},
            results=results,
            num_simulations=num_simulations,
            execution_ms=execution_ms,
            user_id=user_id,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get(self, run_id: int) -> Optional[Dict]:
        row = self.session.get(SimulationRunRow, run_id)
        return self._to_dict(row) if row else None

    def list_runs(
        self, workspace_id: str, limit: int = 50,
    ) -> List[Dict]:
        rows = self.session.execute(
            select(SimulationRunRow)
            .where(SimulationRunRow.workspace_id == workspace_id)
            .order_by(SimulationRunRow.id.desc())
            .limit(limit)
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row: SimulationRunRow) -> Dict:
        return {
            "id": row.id, "workspace_id": row.workspace_id,
            "template_id": row.template_id,
            "scenario_name": row.scenario_name,
            "inputs": row.inputs, "overrides": row.overrides,
            "results": row.results,
            "num_simulations": row.num_simulations,
            "execution_ms": row.execution_ms,
            "user_id": row.user_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
