"""
engine.db.portfolio_repositories — Portfolio Repositories
============================================================
Phase 7: CRUD and aggregation for portfolio assets, debt, NOI,
and EGM exposure tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, func, and_, case
from sqlalchemy.orm import Session

from engine.db.models import (
    PortfolioAssetRow, PortfolioDebtRow,
    PortfolioNOIRow, PortfolioEGMExposureRow,
)


class PortfolioAssetRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, workspace_id: str, name: str, **kwargs) -> Dict:
        row = PortfolioAssetRow(workspace_id=workspace_id, name=name, **kwargs)
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def get(self, asset_id: int) -> Optional[Dict]:
        row = self.session.get(PortfolioAssetRow, asset_id)
        return self._to_dict(row) if row else None

    def list_assets(self, workspace_id: str, active_only: bool = True) -> List[Dict]:
        q = select(PortfolioAssetRow).where(
            PortfolioAssetRow.workspace_id == workspace_id
        )
        if active_only:
            q = q.where(PortfolioAssetRow.is_active == True)
        q = q.order_by(PortfolioAssetRow.id)
        return [self._to_dict(r) for r in self.session.execute(q).scalars().all()]

    def update_value(self, asset_id: int, current_value: float) -> bool:
        result = self.session.execute(
            update(PortfolioAssetRow)
            .where(PortfolioAssetRow.id == asset_id)
            .values(current_value=current_value)
        )
        self.session.flush()
        return result.rowcount > 0

    def deactivate(self, asset_id: int) -> bool:
        result = self.session.execute(
            update(PortfolioAssetRow)
            .where(PortfolioAssetRow.id == asset_id)
            .values(is_active=False)
        )
        self.session.flush()
        return result.rowcount > 0

    def count_by_state(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(
                PortfolioAssetRow.state,
                func.count().label("count"),
                func.sum(PortfolioAssetRow.current_value).label("total_value"),
            )
            .where(and_(
                PortfolioAssetRow.workspace_id == workspace_id,
                PortfolioAssetRow.is_active == True,
            ))
            .group_by(PortfolioAssetRow.state)
        ).all()
        return [{"state": r.state, "count": r.count,
                 "total_value": r.total_value or 0} for r in rows]

    def count_by_property_type(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(
                PortfolioAssetRow.property_type,
                func.count().label("count"),
                func.sum(PortfolioAssetRow.current_value).label("total_value"),
            )
            .where(and_(
                PortfolioAssetRow.workspace_id == workspace_id,
                PortfolioAssetRow.is_active == True,
            ))
            .group_by(PortfolioAssetRow.property_type)
        ).all()
        return [{"property_type": r.property_type, "count": r.count,
                 "total_value": r.total_value or 0} for r in rows]

    def _to_dict(self, row: PortfolioAssetRow) -> Dict:
        return {
            "id": row.id, "workspace_id": row.workspace_id,
            "name": row.name, "asset_type": row.asset_type,
            "property_type": row.property_type,
            "address": row.address, "state": row.state,
            "municipality": row.municipality,
            "acquisition_date": row.acquisition_date,
            "acquisition_cost": row.acquisition_cost,
            "current_value": row.current_value,
            "ownership_type": row.ownership_type,
            "contract_type": row.contract_type,
            "has_gaming": row.has_gaming,
            "terminal_count": row.terminal_count,
            "is_active": row.is_active,
            "metadata": row.metadata_json,
        }


class PortfolioDebtRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, asset_id: int, workspace_id: str, **kwargs) -> Dict:
        row = PortfolioDebtRow(
            asset_id=asset_id, workspace_id=workspace_id, **kwargs,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def list_for_asset(self, asset_id: int) -> List[Dict]:
        rows = self.session.execute(
            select(PortfolioDebtRow)
            .where(and_(
                PortfolioDebtRow.asset_id == asset_id,
                PortfolioDebtRow.is_active == True,
            ))
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def total_debt(self, workspace_id: str) -> float:
        result = self.session.execute(
            select(func.sum(PortfolioDebtRow.current_balance))
            .where(and_(
                PortfolioDebtRow.workspace_id == workspace_id,
                PortfolioDebtRow.is_active == True,
            ))
        ).scalar_one_or_none()
        return result or 0.0

    def maturity_ladder(self, workspace_id: str) -> List[Dict]:
        """Group debt by maturity year."""
        rows = self.session.execute(
            select(PortfolioDebtRow)
            .where(and_(
                PortfolioDebtRow.workspace_id == workspace_id,
                PortfolioDebtRow.is_active == True,
            ))
            .order_by(PortfolioDebtRow.maturity_date)
        ).scalars().all()

        ladder: Dict[str, float] = {}
        for r in rows:
            year = r.maturity_date[:4] if r.maturity_date else "unknown"
            ladder[year] = ladder.get(year, 0) + r.current_balance
        return [{"year": y, "maturing_balance": b} for y, b in sorted(ladder.items())]

    def _to_dict(self, row: PortfolioDebtRow) -> Dict:
        return {
            "id": row.id, "asset_id": row.asset_id,
            "lender": row.lender,
            "original_balance": row.original_balance,
            "current_balance": row.current_balance,
            "annual_rate": row.annual_rate,
            "monthly_payment": row.monthly_payment,
            "maturity_date": row.maturity_date,
            "is_active": row.is_active,
        }


class PortfolioNOIRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, asset_id: int, workspace_id: str,
               period: str, noi_amount: float) -> Dict:
        existing = self.session.execute(
            select(PortfolioNOIRow).where(and_(
                PortfolioNOIRow.asset_id == asset_id,
                PortfolioNOIRow.period == period,
            ))
        ).scalar_one_or_none()

        if existing:
            existing.noi_amount = noi_amount
            self.session.flush()
            return {"id": existing.id, "asset_id": asset_id,
                    "period": period, "noi_amount": noi_amount, "updated": True}

        row = PortfolioNOIRow(
            asset_id=asset_id, workspace_id=workspace_id,
            period=period, noi_amount=noi_amount,
        )
        self.session.add(row)
        self.session.flush()
        return {"id": row.id, "asset_id": asset_id,
                "period": period, "noi_amount": noi_amount, "updated": False}

    def get_history(self, asset_id: int, limit: int = 24) -> List[Dict]:
        rows = self.session.execute(
            select(PortfolioNOIRow)
            .where(PortfolioNOIRow.asset_id == asset_id)
            .order_by(PortfolioNOIRow.period.desc())
            .limit(limit)
        ).scalars().all()
        return [{"period": r.period, "noi_amount": r.noi_amount} for r in rows]

    def total_noi(self, workspace_id: str, period: str) -> float:
        result = self.session.execute(
            select(func.sum(PortfolioNOIRow.noi_amount))
            .where(and_(
                PortfolioNOIRow.workspace_id == workspace_id,
                PortfolioNOIRow.period == period,
            ))
        ).scalar_one_or_none()
        return result or 0.0


class PortfolioEGMExposureRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, asset_id: int, workspace_id: str, **kwargs) -> Dict:
        row = PortfolioEGMExposureRow(
            asset_id=asset_id, workspace_id=workspace_id, **kwargs,
        )
        self.session.add(row)
        self.session.flush()
        return self._to_dict(row)

    def list_for_workspace(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(PortfolioEGMExposureRow)
            .where(PortfolioEGMExposureRow.workspace_id == workspace_id)
        ).scalars().all()
        return [self._to_dict(r) for r in rows]

    def total_gaming_exposure(self, workspace_id: str) -> Dict:
        rows = self.session.execute(
            select(
                func.sum(PortfolioEGMExposureRow.machine_count).label("total_machines"),
                func.sum(PortfolioEGMExposureRow.monthly_net_win).label("total_monthly_nw"),
                func.count().label("gaming_locations"),
            )
            .where(PortfolioEGMExposureRow.workspace_id == workspace_id)
        ).one()
        return {
            "total_machines": rows.total_machines or 0,
            "total_monthly_net_win": rows.total_monthly_nw or 0,
            "gaming_locations": rows.gaming_locations or 0,
        }

    def _to_dict(self, row: PortfolioEGMExposureRow) -> Dict:
        return {
            "id": row.id, "asset_id": row.asset_id,
            "egm_location_id": row.egm_location_id,
            "machine_count": row.machine_count,
            "monthly_net_win": row.monthly_net_win,
            "contract_type": row.contract_type,
        }
