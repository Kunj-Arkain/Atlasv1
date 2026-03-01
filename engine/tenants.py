"""
engine.tenants — Multi-Tenant Identity, RBAC, Secrets & Quotas
================================================================
AUDIT ITEMS #3 + #4 (Impact: 9/10)

Problems in V1:
  - No concept of tenant, workspace, or user identity
  - API keys stored in .env with no scoping or encryption
  - No role-based access control on any operation
  - No resource quotas or cost ceilings
  - Any user can run any pipeline with full access

This module implements:
  - Tenant hierarchy: Organization → Workspace → Project → Job
  - RBAC: Role → Permission mapping with enforcement
  - SecretsVault: Fernet-encrypted per-workspace secrets
  - QuotaEnforcer: pre-flight quota checks before job execution
  - JobQueue: in-memory queue with status tracking

ZERO external dependencies beyond stdlib (uses hashlib for key derivation,
base64 for encoding — no cryptography library needed for basic protection).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set


# ═══════════════════════════════════════════════════════════════
# TENANT HIERARCHY
# ═══════════════════════════════════════════════════════════════

@dataclass
class Organization:
    org_id: str
    name: str
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Workspace:
    workspace_id: str
    org_id: str
    name: str
    created_at: str = ""
    settings: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Project:
    project_id: str
    workspace_id: str
    name: str
    pipeline_type: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# ROLES & PERMISSIONS
# ═══════════════════════════════════════════════════════════════

class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Pipeline
    PIPELINE_CREATE = "pipeline.create"
    PIPELINE_RUN = "pipeline.run"
    PIPELINE_CANCEL = "pipeline.cancel"
    PIPELINE_DELETE = "pipeline.delete"

    # Jobs
    JOB_VIEW = "job.view"
    JOB_APPROVE = "job.approve"
    JOB_CANCEL = "job.cancel"

    # Secrets
    SECRET_CREATE = "secret.create"
    SECRET_VIEW = "secret.view"
    SECRET_DELETE = "secret.delete"

    # Admin
    WORKSPACE_MANAGE = "workspace.manage"
    BILLING_VIEW = "billing.view"
    AUDIT_VIEW = "audit.view"
    CONNECTOR_MANAGE = "connector.manage"
    TOOL_POLICY_MANAGE = "tool_policy.manage"


# Role → Permission mapping
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    Role.OWNER.value: {p.value for p in Permission},  # All permissions
    Role.ADMIN.value: {p.value for p in Permission} - {
        Permission.WORKSPACE_MANAGE.value,
    },
    Role.OPERATOR.value: {
        Permission.PIPELINE_RUN.value,
        Permission.PIPELINE_CANCEL.value,
        Permission.JOB_VIEW.value,
        Permission.JOB_APPROVE.value,
        Permission.JOB_CANCEL.value,
        Permission.SECRET_VIEW.value,
        Permission.BILLING_VIEW.value,
        Permission.AUDIT_VIEW.value,
    },
    Role.VIEWER.value: {
        Permission.JOB_VIEW.value,
        Permission.BILLING_VIEW.value,
        Permission.AUDIT_VIEW.value,
    },
}


@dataclass
class UserIdentity:
    user_id: str
    email: str = ""
    name: str = ""
    org_id: str = ""
    workspace_roles: Dict[str, str] = field(default_factory=dict)
    # workspace_roles: {workspace_id: role_value}

    def role_in(self, workspace_id: str) -> Optional[str]:
        return self.workspace_roles.get(workspace_id)

    def permissions_in(self, workspace_id: str) -> Set[str]:
        role = self.role_in(workspace_id)
        if not role:
            return set()
        return ROLE_PERMISSIONS.get(role, set())


# ═══════════════════════════════════════════════════════════════
# AUTHORIZATION ENGINE
# ═══════════════════════════════════════════════════════════════

class AuthorizationError(Exception):
    def __init__(self, user_id: str, permission: str, workspace_id: str):
        self.user_id = user_id
        self.permission = permission
        self.workspace_id = workspace_id
        super().__init__(
            f"User '{user_id}' lacks permission '{permission}' "
            f"in workspace '{workspace_id}'"
        )


class AuthzEngine:
    """RBAC enforcement engine.

    Usage:
        authz = AuthzEngine()
        authz.register_user(user)

        # Check (returns bool)
        if authz.check(user_id, workspace_id, Permission.PIPELINE_RUN):
            ...

        # Require (raises AuthorizationError)
        authz.require(user_id, workspace_id, Permission.PIPELINE_RUN)
    """

    def __init__(self):
        self._users: Dict[str, UserIdentity] = {}

    def register_user(self, user: UserIdentity):
        self._users[user.user_id] = user

    def get_user(self, user_id: str) -> Optional[UserIdentity]:
        return self._users.get(user_id)

    def check(self, user_id: str, workspace_id: str,
              permission: str) -> bool:
        """Check if user has permission. Returns bool."""
        user = self._users.get(user_id)
        if not user:
            return False
        return permission in user.permissions_in(workspace_id)

    def require(self, user_id: str, workspace_id: str,
                permission: str):
        """Require permission. Raises AuthorizationError if denied."""
        if not self.check(user_id, workspace_id, permission):
            raise AuthorizationError(user_id, permission, workspace_id)

    def user_role(self, user_id: str, workspace_id: str) -> Optional[str]:
        user = self._users.get(user_id)
        if not user:
            return None
        return user.role_in(workspace_id)

    def grant_role(self, user_id: str, workspace_id: str, role: str):
        """Grant a role to a user in a workspace."""
        user = self._users.get(user_id)
        if user:
            user.workspace_roles[workspace_id] = role


# ═══════════════════════════════════════════════════════════════
# SECRETS VAULT — Encrypted per-workspace secrets
# ═══════════════════════════════════════════════════════════════

class SecretsVault:
    """Per-workspace encrypted secrets storage.

    Encryption backends (in order of preference):
      1. Fernet (cryptography library) — production recommended
      2. HMAC-CTR (stdlib) — AES-equivalent stream cipher using
         HMAC-SHA256 as a PRF in counter mode. Cryptographically
         sound, unlike the V2 XOR approach.

    Secrets are scoped to workspace — no cross-workspace leakage.
    Master key is REQUIRED — no default fallback (P0.3 fix).
    """

    def __init__(self, master_key: Optional[str] = None,
                 storage_path: Optional[Path] = None):
        env_key = os.getenv("SECRETS_MASTER_KEY", "")
        self._master_key = master_key or env_key
        if not self._master_key:
            raise ValueError(
                "SecretsVault requires a master key. Set SECRETS_MASTER_KEY "
                "env var or pass master_key= to constructor. "
                "No default key is provided (security policy)."
            )
        self._storage_path = storage_path
        self._store: Dict[str, Dict[str, str]] = {}

        # Try Fernet first, fallback to HMAC-CTR
        self._use_fernet = False
        try:
            from cryptography.fernet import Fernet as _Fernet
            self._use_fernet = True
        except ImportError:
            pass

        if storage_path and storage_path.exists():
            self._load()

    def _derive_key(self, workspace_id: str) -> bytes:
        """Derive a per-workspace encryption key via HMAC-SHA256."""
        return hmac.new(
            self._master_key.encode(), workspace_id.encode(), hashlib.sha256
        ).digest()

    def _encrypt(self, plaintext: str, workspace_id: str) -> str:
        """Encrypt using Fernet or HMAC-CTR."""
        key = self._derive_key(workspace_id)

        if self._use_fernet:
            from cryptography.fernet import Fernet
            # Fernet needs url-safe base64 32-byte key
            fernet_key = base64.urlsafe_b64encode(key)
            f = Fernet(fernet_key)
            return f.encrypt(plaintext.encode("utf-8")).decode("ascii")

        # Fallback: HMAC-CTR — generates keystream via HMAC(key, counter)
        data = plaintext.encode("utf-8")
        nonce = os.urandom(16)
        keystream = self._hmac_ctr_keystream(key, nonce, len(data))
        encrypted = bytes(d ^ k for d, k in zip(data, keystream))
        return base64.b64encode(nonce + encrypted).decode("ascii")

    def _decrypt(self, ciphertext: str, workspace_id: str) -> str:
        """Decrypt using Fernet or HMAC-CTR."""
        key = self._derive_key(workspace_id)

        if self._use_fernet:
            from cryptography.fernet import Fernet
            fernet_key = base64.urlsafe_b64encode(key)
            f = Fernet(fernet_key)
            return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")

        # HMAC-CTR fallback
        raw = base64.b64decode(ciphertext)
        nonce = raw[:16]
        encrypted = raw[16:]
        keystream = self._hmac_ctr_keystream(key, nonce, len(encrypted))
        decrypted = bytes(d ^ k for d, k in zip(encrypted, keystream))
        return decrypted.decode("utf-8")

    @staticmethod
    def _hmac_ctr_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
        """Generate keystream using HMAC-SHA256 in counter mode."""
        stream = b""
        counter = 0
        while len(stream) < length:
            block_input = nonce + counter.to_bytes(4, "big")
            block = hmac.new(key, block_input, hashlib.sha256).digest()
            stream += block
            counter += 1
        return stream[:length]

    def set_secret(self, workspace_id: str, key: str, value: str):
        """Store an encrypted secret scoped to workspace."""
        if workspace_id not in self._store:
            self._store[workspace_id] = {}
        self._store[workspace_id][key] = self._encrypt(value, workspace_id)
        self._persist()

    def get_secret(self, workspace_id: str, key: str) -> Optional[str]:
        """Retrieve a decrypted secret. Returns None if not found."""
        ws_secrets = self._store.get(workspace_id, {})
        encrypted = ws_secrets.get(key)
        if encrypted is None:
            return None
        return self._decrypt(encrypted, workspace_id)

    def delete_secret(self, workspace_id: str, key: str) -> bool:
        ws_secrets = self._store.get(workspace_id, {})
        if key in ws_secrets:
            del ws_secrets[key]
            self._persist()
            return True
        return False

    def list_keys(self, workspace_id: str) -> List[str]:
        """List secret keys (NOT values) for a workspace."""
        return list(self._store.get(workspace_id, {}).keys())

    def ephemeral_env(self, workspace_id: str, keys: List[str]) -> Dict[str, str]:
        """Get decrypted secrets as env dict. Use for per-job credential scoping.

        Usage:
            env = vault.ephemeral_env("ws1", ["OPENAI_API_KEY", "DB_URL"])
            os.environ.update(env)  # Set for job duration only
        """
        result = {}
        for k in keys:
            val = self.get_secret(workspace_id, k)
            if val is not None:
                result[k] = val
        return result

    def _persist(self):
        if not self._storage_path:
            return
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._storage_path.write_text(
                json.dumps(self._store, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _load(self):
        try:
            self._store = json.loads(self._storage_path.read_text())
        except (OSError, json.JSONDecodeError):
            self._store = {}


# ═══════════════════════════════════════════════════════════════
# QUOTAS
# ═══════════════════════════════════════════════════════════════

@dataclass
class TenantQuota:
    """Resource quotas per workspace."""
    max_concurrent_jobs: int = 5
    monthly_cost_ceiling_usd: float = 500.0
    max_storage_bytes: int = 10 * 1024**3      # 10GB
    max_pipeline_timeout_s: int = 7200
    max_agents_per_pipeline: int = 10
    max_tools_per_pipeline: int = 20
    allowed_llm_tiers: List[str] = field(
        default_factory=lambda: ["light", "heavy", "premium"]
    )


class QuotaExceededError(Exception):
    def __init__(self, quota_type: str, limit: Any, current: Any):
        self.quota_type = quota_type
        self.limit = limit
        self.current = current
        super().__init__(
            f"Quota exceeded: {quota_type} (limit={limit}, current={current})"
        )


class QuotaEnforcer:
    """Pre-flight quota checks before job execution."""

    def __init__(self):
        self._quotas: Dict[str, TenantQuota] = {}
        self._active_jobs: Dict[str, int] = {}     # workspace_id → count
        self._monthly_spend: Dict[str, float] = {}  # workspace_id → USD

    def set_quota(self, workspace_id: str, quota: TenantQuota):
        self._quotas[workspace_id] = quota

    def get_quota(self, workspace_id: str) -> TenantQuota:
        return self._quotas.get(workspace_id, TenantQuota())

    def check_can_run(self, workspace_id: str,
                      num_agents: int = 1,
                      num_tools: int = 1,
                      llm_tier: str = "heavy") -> bool:
        """Check all quotas. Raises QuotaExceededError on violation."""
        quota = self.get_quota(workspace_id)
        active = self._active_jobs.get(workspace_id, 0)
        spend = self._monthly_spend.get(workspace_id, 0.0)

        if active >= quota.max_concurrent_jobs:
            raise QuotaExceededError(
                "concurrent_jobs", quota.max_concurrent_jobs, active
            )

        if spend >= quota.monthly_cost_ceiling_usd:
            raise QuotaExceededError(
                "monthly_cost", quota.monthly_cost_ceiling_usd, spend
            )

        if num_agents > quota.max_agents_per_pipeline:
            raise QuotaExceededError(
                "agents_per_pipeline", quota.max_agents_per_pipeline, num_agents
            )

        if num_tools > quota.max_tools_per_pipeline:
            raise QuotaExceededError(
                "tools_per_pipeline", quota.max_tools_per_pipeline, num_tools
            )

        if llm_tier not in quota.allowed_llm_tiers:
            raise QuotaExceededError(
                "llm_tier", quota.allowed_llm_tiers, llm_tier
            )

        return True

    def job_started(self, workspace_id: str):
        self._active_jobs[workspace_id] = self._active_jobs.get(workspace_id, 0) + 1

    def job_finished(self, workspace_id: str):
        current = self._active_jobs.get(workspace_id, 0)
        self._active_jobs[workspace_id] = max(0, current - 1)

    def add_spend(self, workspace_id: str, amount_usd: float):
        self._monthly_spend[workspace_id] = (
            self._monthly_spend.get(workspace_id, 0.0) + amount_usd
        )

    def reset_monthly(self, workspace_id: str):
        self._monthly_spend[workspace_id] = 0.0


# ═══════════════════════════════════════════════════════════════
# JOB QUEUE
# ═══════════════════════════════════════════════════════════════

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"


@dataclass
class Job:
    job_id: str
    workspace_id: str
    user_id: str
    pipeline_type: str
    status: str = JobStatus.QUEUED.value
    config: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self):
        if not self.job_id:
            self.job_id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "pipeline_type": self.pipeline_type,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class JobQueue:
    """Job queue with optional SQLite persistence.

    P1.1 FIX: V2 was in-memory only — service restart = lost jobs.
    Now persists to SQLite when db_path is provided.
    Falls back to in-memory for dev/testing.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._jobs: Dict[str, Job] = {}
        self._db_path = db_path

        if db_path:
            import sqlite3
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    pipeline_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    config TEXT DEFAULT '{}',
                    result TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT DEFAULT '',
                    completed_at TEXT DEFAULT ''
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_ws ON jobs(workspace_id)"
            )
            self._conn.commit()
            self._load_from_db()
        else:
            self._conn = None

    def _load_from_db(self):
        if not self._conn:
            return
        for row in self._conn.execute("SELECT * FROM jobs"):
            job = Job(
                job_id=row[0], workspace_id=row[1], user_id=row[2],
                pipeline_type=row[3], status=row[4],
                config=json.loads(row[5]) if row[5] else {},
                result=row[6], error=row[7],
                created_at=row[8], started_at=row[9] or "",
                completed_at=row[10] or "",
            )
            self._jobs[job.job_id] = job

    def _persist_job(self, job: Job):
        if not self._conn:
            return
        self._conn.execute("""
            INSERT OR REPLACE INTO jobs
                (job_id, workspace_id, user_id, pipeline_type, status,
                 config, result, error, created_at, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job.job_id, job.workspace_id, job.user_id, job.pipeline_type,
            job.status, json.dumps(job.config, default=str),
            json.dumps(job.result, default=str) if job.result else "",
            job.error, job.created_at, job.started_at, job.completed_at,
        ))
        self._conn.commit()

    def submit(self, workspace_id: str, user_id: str,
               pipeline_type: str, config: Optional[Dict] = None) -> Job:
        job = Job(
            job_id=uuid.uuid4().hex[:12],
            workspace_id=workspace_id,
            user_id=user_id,
            pipeline_type=pipeline_type,
            config=config or {},
        )
        self._jobs[job.job_id] = job
        self._persist_job(job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self, workspace_id: str, limit: int = 50) -> List[Job]:
        return [j for j in self._jobs.values()
                if j.workspace_id == workspace_id][:limit]

    def update_status(self, job_id: str, status: str,
                      error: str = "", result: Any = None):
        job = self._jobs.get(job_id)
        if job:
            job.status = status
            if error:
                job.error = error
            if result is not None:
                job.result = result
            if status == JobStatus.RUNNING.value:
                job.started_at = datetime.now(timezone.utc).isoformat()
            if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value,
                          JobStatus.CANCELLED.value):
                job.completed_at = datetime.now(timezone.utc).isoformat()
            self._persist_job(job)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
            job.status = JobStatus.CANCELLED.value
            job.completed_at = datetime.now(timezone.utc).isoformat()
            self._persist_job(job)
            return True
        return False

    def pending_jobs(self, workspace_id: str) -> List[Job]:
        return [j for j in self._jobs.values()
                if j.workspace_id == workspace_id
                and j.status == JobStatus.QUEUED.value]
