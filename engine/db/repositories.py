"""
engine.db.repositories — Repository Pattern
=============================================
Phase 0A: Translates between existing in-memory dataclasses and DB rows.

Each repository:
  - Accepts/returns the engine's existing dataclass types
  - Handles the ORM mapping internally
  - Provides both single-item and batch operations
  - Is workspace-scoped where applicable

The existing engine code (tenants.py, auth.py, etc.) doesn't need to change —
repositories adapt the interface.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, delete, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from engine.db.models import (
    OrganizationRow, WorkspaceRow, UserRow, MembershipRow,
    APIKeyRow, AuditLogRow, JobRow,
    AgentConfigRow, AgentConfigVersionRow, ModelRouteRow,
    ToolPolicyRow, PipelineDefRow, StrategyWeightsRow,
    ToolRunRow,
)
from engine.tenants import (
    Organization, Workspace, Project, UserIdentity, Job, JobStatus,
)
from engine.auth import APIKeyRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# ORGANIZATION REPO
# ═══════════════════════════════════════════════════════════════

class OrganizationRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, org: Organization) -> Organization:
        row = OrganizationRow(
            id=org.org_id,
            name=org.name,
            metadata_=org.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return org

    def get(self, org_id: str) -> Optional[Organization]:
        row = self.session.get(OrganizationRow, org_id)
        if not row:
            return None
        return Organization(
            org_id=row.id,
            name=row.name,
            created_at=row.created_at.isoformat() if row.created_at else "",
            metadata=row.metadata_ or {},
        )

    def list_all(self) -> List[Organization]:
        rows = self.session.execute(select(OrganizationRow)).scalars().all()
        return [
            Organization(
                org_id=r.id, name=r.name,
                created_at=r.created_at.isoformat() if r.created_at else "",
                metadata=r.metadata_ or {},
            )
            for r in rows
        ]


# ═══════════════════════════════════════════════════════════════
# WORKSPACE REPO
# ═══════════════════════════════════════════════════════════════

class WorkspaceRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, ws: Workspace) -> Workspace:
        row = WorkspaceRow(
            id=ws.workspace_id,
            org_id=ws.org_id,
            name=ws.name,
            settings=ws.settings,
        )
        self.session.add(row)
        self.session.flush()
        return ws

    def get(self, workspace_id: str) -> Optional[Workspace]:
        row = self.session.get(WorkspaceRow, workspace_id)
        if not row:
            return None
        return Workspace(
            workspace_id=row.id,
            org_id=row.org_id,
            name=row.name,
            created_at=row.created_at.isoformat() if row.created_at else "",
            settings=row.settings or {},
        )

    def list_by_org(self, org_id: str) -> List[Workspace]:
        rows = self.session.execute(
            select(WorkspaceRow).where(WorkspaceRow.org_id == org_id)
        ).scalars().all()
        return [
            Workspace(
                workspace_id=r.id, org_id=r.org_id, name=r.name,
                created_at=r.created_at.isoformat() if r.created_at else "",
                settings=r.settings or {},
            )
            for r in rows
        ]


# ═══════════════════════════════════════════════════════════════
# USER REPO
# ═══════════════════════════════════════════════════════════════

class UserRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, user: UserIdentity, password_hash: str = "") -> UserIdentity:
        row = UserRow(
            id=user.user_id,
            email=user.email,
            name=user.name,
            org_id=user.org_id,
            password_hash=password_hash,
        )
        self.session.add(row)
        self.session.flush()

        # Create memberships
        for ws_id, role in user.workspace_roles.items():
            mem = MembershipRow(
                user_id=user.user_id,
                workspace_id=ws_id,
                role=role,
            )
            self.session.add(mem)
        self.session.flush()
        return user

    def get(self, user_id: str) -> Optional[UserIdentity]:
        row = self.session.get(UserRow, user_id)
        if not row:
            return None

        # Load memberships
        mems = self.session.execute(
            select(MembershipRow).where(MembershipRow.user_id == user_id)
        ).scalars().all()
        workspace_roles = {m.workspace_id: m.role for m in mems}

        return UserIdentity(
            user_id=row.id,
            email=row.email,
            name=row.name,
            org_id=row.org_id,
            workspace_roles=workspace_roles,
        )

    def get_by_email(self, email: str) -> Optional[UserIdentity]:
        row = self.session.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if not row:
            return None
        return self.get(row.id)

    def get_password_hash(self, user_id: str) -> Optional[str]:
        row = self.session.get(UserRow, user_id)
        return row.password_hash if row else None

    def set_role(self, user_id: str, workspace_id: str, role: str):
        """Set or update a user's role in a workspace."""
        stmt = pg_insert(MembershipRow.__table__).values(
            user_id=user_id, workspace_id=workspace_id, role=role,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_user_workspace",
            set_={"role": role},
        )
        self.session.execute(stmt)
        self.session.flush()

    def list_by_workspace(self, workspace_id: str) -> List[UserIdentity]:
        mems = self.session.execute(
            select(MembershipRow).where(MembershipRow.workspace_id == workspace_id)
        ).scalars().all()

        users = []
        for m in mems:
            user = self.get(m.user_id)
            if user:
                users.append(user)
        return users


# ═══════════════════════════════════════════════════════════════
# API KEY REPO
# ═══════════════════════════════════════════════════════════════

class APIKeyRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, record: APIKeyRecord) -> APIKeyRecord:
        row = APIKeyRow(
            id=record.key_id,
            key_hash=record.key_hash,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            scopes=record.scopes,
            created_at=_utcnow(),
            expires_at=(
                datetime.fromisoformat(record.expires_at)
                if record.expires_at else None
            ),
        )
        self.session.add(row)
        self.session.flush()
        return record

    def get_by_hash(self, key_hash: str) -> Optional[APIKeyRecord]:
        row = self.session.execute(
            select(APIKeyRow).where(APIKeyRow.key_hash == key_hash)
        ).scalar_one_or_none()
        if not row:
            return None
        return APIKeyRecord(
            key_id=row.id,
            key_hash=row.key_hash,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            scopes=row.scopes or [],
            created_at=row.created_at.isoformat() if row.created_at else "",
            expires_at=row.expires_at.isoformat() if row.expires_at else "",
            revoked=row.revoked,
        )

    def revoke(self, key_id: str) -> bool:
        result = self.session.execute(
            update(APIKeyRow)
            .where(APIKeyRow.id == key_id)
            .values(revoked=True)
        )
        self.session.flush()
        return result.rowcount > 0


# ═══════════════════════════════════════════════════════════════
# AUDIT LOG REPO
# ═══════════════════════════════════════════════════════════════

class AuditLogRepo:
    def __init__(self, session: Session):
        self.session = session

    def append(
        self, workspace_id: str, action: str, resource: str = "",
        outcome: str = "success", user_id: str = "",
        details: Optional[Dict] = None,
    ) -> int:
        row = AuditLogRow(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            resource=resource,
            outcome=outcome,
            details=details or {},
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_entries(
        self, workspace_id: str, limit: int = 100, offset: int = 0,
        action_filter: Optional[str] = None,
    ) -> List[Dict]:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.workspace_id == workspace_id)
            .order_by(AuditLogRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if action_filter:
            stmt = stmt.where(AuditLogRow.action == action_filter)
        rows = self.session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "workspace_id": r.workspace_id,
                "user_id": r.user_id,
                "action": r.action,
                "resource": r.resource,
                "outcome": r.outcome,
                "details": r.details,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]

    def count(self, workspace_id: str) -> int:
        result = self.session.execute(
            select(func.count(AuditLogRow.id))
            .where(AuditLogRow.workspace_id == workspace_id)
        )
        return result.scalar_one()


# ═══════════════════════════════════════════════════════════════
# JOB REPO
# ═══════════════════════════════════════════════════════════════

class JobRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, job: Job) -> Job:
        row = JobRow(
            id=job.job_id,
            workspace_id=job.workspace_id,
            user_id=job.user_id,
            pipeline_type=job.pipeline_type,
            status=job.status,
            config=job.config,
            error=job.error,
        )
        self.session.add(row)
        self.session.flush()
        job.created_at = row.created_at.isoformat() if row.created_at else ""
        return job

    def get(self, job_id: str) -> Optional[Job]:
        row = self.session.get(JobRow, job_id)
        if not row:
            return None
        return self._row_to_job(row)

    def list_by_workspace(
        self, workspace_id: str, limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> List[Job]:
        stmt = (
            select(JobRow)
            .where(JobRow.workspace_id == workspace_id)
            .order_by(JobRow.created_at.desc())
            .limit(limit)
        )
        if status_filter:
            stmt = stmt.where(JobRow.status == status_filter)
        rows = self.session.execute(stmt).scalars().all()
        return [self._row_to_job(r) for r in rows]

    def update_status(
        self, job_id: str, status: str,
        error: str = "", result: Any = None,
    ):
        values: Dict[str, Any] = {"status": status}
        if error:
            values["error"] = error
        if result is not None:
            values["result"] = result
        if status == JobStatus.RUNNING.value:
            values["started_at"] = _utcnow()
        if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value,
                       JobStatus.CANCELLED.value):
            values["completed_at"] = _utcnow()

        self.session.execute(
            update(JobRow).where(JobRow.id == job_id).values(**values)
        )
        self.session.flush()

    def pending_jobs(self, workspace_id: str) -> List[Job]:
        return self.list_by_workspace(
            workspace_id, status_filter=JobStatus.QUEUED.value
        )

    def _row_to_job(self, row: JobRow) -> Job:
        return Job(
            job_id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            pipeline_type=row.pipeline_type,
            status=row.status,
            config=row.config or {},
            result=row.result,
            error=row.error or "",
            created_at=row.created_at.isoformat() if row.created_at else "",
            started_at=row.started_at.isoformat() if row.started_at else "",
            completed_at=row.completed_at.isoformat() if row.completed_at else "",
        )


# ═══════════════════════════════════════════════════════════════
# AGENT CONFIG REPO (Phase 1 — ready to use)
# ═══════════════════════════════════════════════════════════════

class AgentConfigRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(
        self, workspace_id: str, agent_name: str,
        config: Dict[str, Any], changed_by: str = "",
        change_reason: str = "",
    ) -> Dict:
        """Create or update an agent config. Auto-versions."""
        existing = self.session.execute(
            select(AgentConfigRow).where(
                and_(
                    AgentConfigRow.workspace_id == workspace_id,
                    AgentConfigRow.agent_name == agent_name,
                )
            )
        ).scalar_one_or_none()

        if existing:
            # Snapshot current version
            self._snapshot_version(existing, changed_by, change_reason)
            # Update
            for key, val in config.items():
                if hasattr(existing, key):
                    setattr(existing, key, val)
            existing.version += 1
            existing.created_by = changed_by
            self.session.flush()
            return self._row_to_dict(existing)
        else:
            row = AgentConfigRow(
                workspace_id=workspace_id,
                agent_name=agent_name,
                version=1,
                created_by=changed_by,
                **{k: v for k, v in config.items() if hasattr(AgentConfigRow, k)},
            )
            self.session.add(row)
            self.session.flush()
            return self._row_to_dict(row)

    def get(self, workspace_id: str, agent_name: str) -> Optional[Dict]:
        row = self.session.execute(
            select(AgentConfigRow).where(
                and_(
                    AgentConfigRow.workspace_id == workspace_id,
                    AgentConfigRow.agent_name == agent_name,
                )
            )
        ).scalar_one_or_none()
        return self._row_to_dict(row) if row else None

    def list_by_workspace(self, workspace_id: str) -> List[Dict]:
        rows = self.session.execute(
            select(AgentConfigRow).where(
                AgentConfigRow.workspace_id == workspace_id
            )
        ).scalars().all()
        return [self._row_to_dict(r) for r in rows]

    def get_version_history(
        self, workspace_id: str, agent_name: str
    ) -> List[Dict]:
        config = self.session.execute(
            select(AgentConfigRow).where(
                and_(
                    AgentConfigRow.workspace_id == workspace_id,
                    AgentConfigRow.agent_name == agent_name,
                )
            )
        ).scalar_one_or_none()
        if not config:
            return []

        rows = self.session.execute(
            select(AgentConfigVersionRow)
            .where(AgentConfigVersionRow.agent_config_id == config.id)
            .order_by(AgentConfigVersionRow.version.desc())
        ).scalars().all()
        return [
            {
                "version": r.version,
                "config_snapshot": r.config_snapshot,
                "changed_by": r.changed_by,
                "change_reason": r.change_reason,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]

    def rollback(
        self, workspace_id: str, agent_name: str,
        target_version: int, changed_by: str = "",
    ) -> Optional[Dict]:
        """Rollback to a previous version."""
        config = self.session.execute(
            select(AgentConfigRow).where(
                and_(
                    AgentConfigRow.workspace_id == workspace_id,
                    AgentConfigRow.agent_name == agent_name,
                )
            )
        ).scalar_one_or_none()
        if not config:
            return None

        # Find the target version snapshot
        version_row = self.session.execute(
            select(AgentConfigVersionRow).where(
                and_(
                    AgentConfigVersionRow.agent_config_id == config.id,
                    AgentConfigVersionRow.version == target_version,
                )
            )
        ).scalar_one_or_none()
        if not version_row:
            return None

        snapshot = version_row.config_snapshot
        return self.upsert(
            workspace_id, agent_name, snapshot,
            changed_by=changed_by,
            change_reason=f"Rollback to version {target_version}",
        )

    def _snapshot_version(
        self, row: AgentConfigRow,
        changed_by: str, change_reason: str,
    ):
        snapshot = self._row_to_dict(row)
        version = AgentConfigVersionRow(
            agent_config_id=row.id,
            version=row.version,
            config_snapshot=snapshot,
            changed_by=changed_by,
            change_reason=change_reason,
        )
        self.session.add(version)

    def _row_to_dict(self, row: AgentConfigRow) -> Dict:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "agent_name": row.agent_name,
            "version": row.version,
            "model_provider": row.model_provider,
            "model_name": row.model_name,
            "max_tokens": row.max_tokens,
            "temperature": row.temperature,
            "timeout_sec": row.timeout_sec,
            "retry_count": row.retry_count,
            "retry_backoff": row.retry_backoff,
            "tool_allowlist": row.tool_allowlist,
            "prompt_template": row.prompt_template,
            "output_schema": row.output_schema,
            "agent_weight": row.agent_weight,
            "enabled": row.enabled,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "created_by": row.created_by,
        }


# ═══════════════════════════════════════════════════════════════
# TOOL RUN REPO (Phase 2 — ready to use)
# ═══════════════════════════════════════════════════════════════

class ToolRunRepo:
    def __init__(self, session: Session):
        self.session = session

    def record(
        self, workspace_id: str, tool_name: str,
        inputs: Dict, outputs: Dict,
        user_id: str = "", execution_ms: int = 0,
    ) -> int:
        row = ToolRunRow(
            workspace_id=workspace_id,
            user_id=user_id,
            tool_name=tool_name,
            inputs=inputs,
            outputs=outputs,
            execution_ms=execution_ms,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_runs(
        self, workspace_id: str, tool_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        stmt = (
            select(ToolRunRow)
            .where(ToolRunRow.workspace_id == workspace_id)
            .order_by(ToolRunRow.created_at.desc())
            .limit(limit)
        )
        if tool_name:
            stmt = stmt.where(ToolRunRow.tool_name == tool_name)
        rows = self.session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "tool_name": r.tool_name,
                "inputs": r.inputs,
                "outputs": r.outputs,
                "user_id": r.user_id,
                "execution_ms": r.execution_ms,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
