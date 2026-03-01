"""
tests/test_phase0a.py — Database Layer Tests
===============================================
Tests all repositories against a real SQLite database.
SQLAlchemy makes this portable — same models, different driver.

Run: pytest tests/test_phase0a.py -v
"""

import os
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ── Setup test environment before imports ────────────────────
os.environ["DATABASE_URL"] = "sqlite://"  # In-memory SQLite
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "test-secret"

from engine.db.models import Base
from engine.db.settings import Settings, get_settings, reset_settings
from engine.db.session import check_db_connection
from engine.db.repositories import (
    OrganizationRepo, WorkspaceRepo, UserRepo,
    APIKeyRepo, AuditLogRepo, JobRepo,
    AgentConfigRepo, ToolRunRepo,
)
from engine.tenants import Organization, Workspace, UserIdentity, Job, JobStatus
from engine.auth import APIKeyRecord


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def engine():
    """Create a test engine with SQLite in-memory."""
    eng = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    """Per-test session with automatic rollback."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    sess = Session()

    yield sess

    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def seed_org_workspace(session):
    """Seed an org and workspace for tests that need them."""
    org_repo = OrganizationRepo(session)
    ws_repo = WorkspaceRepo(session)

    org = org_repo.create(Organization(org_id="org1", name="Test Org"))
    ws = ws_repo.create(Workspace(
        workspace_id="ws1", org_id="org1", name="Test Workspace"
    ))
    session.flush()
    return org, ws


# ═══════════════════════════════════════════════════════════════
# SETTINGS TESTS
# ═══════════════════════════════════════════════════════════════

class TestSettings:
    def test_defaults(self):
        reset_settings()
        s = Settings()
        assert s.env == "development"
        assert s.db.pool_size == 10

    def test_dsn_construction(self):
        s = Settings()
        # When DATABASE_URL is set, it takes precedence
        assert s.db.dsn  # Should return something

    def test_redis_dsn(self):
        s = Settings()
        dsn = s.redis.dsn
        assert "redis://" in dsn

    def test_is_development(self):
        s = Settings()
        assert s.is_development


# ═══════════════════════════════════════════════════════════════
# ORGANIZATION REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestOrganizationRepo:
    def test_create_and_get(self, session):
        repo = OrganizationRepo(session)
        org = Organization(org_id="org_test", name="Acme Corp")
        repo.create(org)
        session.flush()

        fetched = repo.get("org_test")
        assert fetched is not None
        assert fetched.name == "Acme Corp"

    def test_get_nonexistent(self, session):
        repo = OrganizationRepo(session)
        assert repo.get("nonexistent") is None

    def test_list_all(self, session):
        repo = OrganizationRepo(session)
        repo.create(Organization(org_id="o1", name="Org 1"))
        repo.create(Organization(org_id="o2", name="Org 2"))
        session.flush()

        orgs = repo.list_all()
        assert len(orgs) >= 2


# ═══════════════════════════════════════════════════════════════
# WORKSPACE REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestWorkspaceRepo:
    def test_create_and_get(self, session, seed_org_workspace):
        repo = WorkspaceRepo(session)
        ws = repo.get("ws1")
        assert ws is not None
        assert ws.name == "Test Workspace"
        assert ws.org_id == "org1"

    def test_list_by_org(self, session, seed_org_workspace):
        repo = WorkspaceRepo(session)
        repo.create(Workspace(
            workspace_id="ws2", org_id="org1", name="Second Workspace"
        ))
        session.flush()

        workspaces = repo.list_by_org("org1")
        assert len(workspaces) >= 2


# ═══════════════════════════════════════════════════════════════
# USER REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestUserRepo:
    def test_create_with_roles(self, session, seed_org_workspace):
        repo = UserRepo(session)
        user = UserIdentity(
            user_id="u1", email="alice@test.com", name="Alice",
            org_id="org1",
            workspace_roles={"ws1": "owner"},
        )
        repo.create(user, password_hash="hashed_pw")
        session.flush()

        fetched = repo.get("u1")
        assert fetched is not None
        assert fetched.email == "alice@test.com"
        assert fetched.workspace_roles["ws1"] == "owner"

    def test_get_by_email(self, session, seed_org_workspace):
        repo = UserRepo(session)
        user = UserIdentity(
            user_id="u2", email="bob@test.com", name="Bob",
            org_id="org1", workspace_roles={"ws1": "viewer"},
        )
        repo.create(user)
        session.flush()

        fetched = repo.get_by_email("bob@test.com")
        assert fetched is not None
        assert fetched.user_id == "u2"

    def test_get_password_hash(self, session, seed_org_workspace):
        repo = UserRepo(session)
        user = UserIdentity(
            user_id="u3", email="carol@test.com", org_id="org1",
        )
        repo.create(user, password_hash="secret_hash")
        session.flush()

        pw = repo.get_password_hash("u3")
        assert pw == "secret_hash"

    def test_set_role(self, session, seed_org_workspace):
        repo = UserRepo(session)
        user = UserIdentity(
            user_id="u4", email="dave@test.com", org_id="org1",
            workspace_roles={"ws1": "viewer"},
        )
        repo.create(user)
        session.flush()

        # Upgrade role
        repo.set_role("u4", "ws1", "admin")
        session.flush()

        fetched = repo.get("u4")
        assert fetched.workspace_roles["ws1"] == "admin"

    def test_list_by_workspace(self, session, seed_org_workspace):
        repo = UserRepo(session)
        for i in range(3):
            user = UserIdentity(
                user_id=f"wsu{i}", email=f"ws_user{i}@test.com",
                org_id="org1", workspace_roles={"ws1": "viewer"},
            )
            repo.create(user)
        session.flush()

        users = repo.list_by_workspace("ws1")
        assert len(users) >= 3


# ═══════════════════════════════════════════════════════════════
# API KEY REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestAPIKeyRepo:
    def test_create_and_lookup(self, session, seed_org_workspace):
        # Create user first
        user_repo = UserRepo(session)
        user_repo.create(UserIdentity(
            user_id="ak_user", email="ak@test.com", org_id="org1",
        ))
        session.flush()

        repo = APIKeyRepo(session)
        record = APIKeyRecord(
            key_id="ak_001", key_hash="abc123hash",
            workspace_id="ws1", user_id="ak_user",
            scopes=["pipeline.run"],
        )
        repo.create(record)
        session.flush()

        fetched = repo.get_by_hash("abc123hash")
        assert fetched is not None
        assert fetched.key_id == "ak_001"
        assert fetched.scopes == ["pipeline.run"]

    def test_revoke(self, session, seed_org_workspace):
        user_repo = UserRepo(session)
        user_repo.create(UserIdentity(
            user_id="ak_user2", email="ak2@test.com", org_id="org1",
        ))
        session.flush()

        repo = APIKeyRepo(session)
        record = APIKeyRecord(
            key_id="ak_002", key_hash="def456hash",
            workspace_id="ws1", user_id="ak_user2",
        )
        repo.create(record)
        session.flush()

        assert repo.revoke("ak_002") is True

        fetched = repo.get_by_hash("def456hash")
        assert fetched.revoked is True


# ═══════════════════════════════════════════════════════════════
# AUDIT LOG REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestAuditLogRepo:
    def test_append_and_list(self, session):
        repo = AuditLogRepo(session)
        repo.append(
            workspace_id="ws1", action="pipeline.run",
            resource="pipeline:egm", outcome="success",
            user_id="u1", details={"pipeline_id": "p1"},
        )
        repo.append(
            workspace_id="ws1", action="config.update",
            resource="agent:analyzer", outcome="success",
            user_id="u1",
        )
        session.flush()

        entries = repo.list_entries("ws1")
        assert len(entries) >= 2
        # Most recent first
        assert entries[0]["action"] == "config.update"

    def test_filter_by_action(self, session):
        repo = AuditLogRepo(session)
        repo.append(workspace_id="ws_f", action="auth.login", user_id="u1")
        repo.append(workspace_id="ws_f", action="auth.login", user_id="u2")
        repo.append(workspace_id="ws_f", action="pipeline.run", user_id="u1")
        session.flush()

        entries = repo.list_entries("ws_f", action_filter="auth.login")
        assert len(entries) == 2

    def test_count(self, session):
        repo = AuditLogRepo(session)
        repo.append(workspace_id="ws_c", action="test1")
        repo.append(workspace_id="ws_c", action="test2")
        repo.append(workspace_id="ws_c", action="test3")
        session.flush()

        assert repo.count("ws_c") == 3


# ═══════════════════════════════════════════════════════════════
# JOB REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestJobRepo:
    def test_create_and_get(self, session, seed_org_workspace):
        repo = JobRepo(session)
        job = Job(
            job_id="j001", workspace_id="ws1",
            user_id="u1", pipeline_type="egm_analysis",
            config={"location_id": 42},
        )
        repo.create(job)
        session.flush()

        fetched = repo.get("j001")
        assert fetched is not None
        assert fetched.pipeline_type == "egm_analysis"
        assert fetched.status == "queued"
        assert fetched.config["location_id"] == 42

    def test_update_status_running(self, session, seed_org_workspace):
        repo = JobRepo(session)
        job = Job(
            job_id="j002", workspace_id="ws1",
            user_id="u1", pipeline_type="re_filter",
        )
        repo.create(job)
        session.flush()

        repo.update_status("j002", JobStatus.RUNNING.value)
        session.flush()

        fetched = repo.get("j002")
        assert fetched.status == "running"
        assert fetched.started_at != ""

    def test_update_status_completed(self, session, seed_org_workspace):
        repo = JobRepo(session)
        job = Job(
            job_id="j003", workspace_id="ws1",
            user_id="u1", pipeline_type="egm_analysis",
        )
        repo.create(job)
        session.flush()

        repo.update_status(
            "j003", JobStatus.COMPLETED.value,
            result={"irr": 0.18, "recommendation": "GO"},
        )
        session.flush()

        fetched = repo.get("j003")
        assert fetched.status == "completed"
        assert fetched.completed_at != ""

    def test_update_status_failed(self, session, seed_org_workspace):
        repo = JobRepo(session)
        job = Job(
            job_id="j004", workspace_id="ws1",
            user_id="u1", pipeline_type="contract_sim",
        )
        repo.create(job)
        session.flush()

        repo.update_status("j004", JobStatus.FAILED.value, error="Out of memory")
        session.flush()

        fetched = repo.get("j004")
        assert fetched.status == "failed"
        assert "memory" in fetched.error

    def test_list_by_workspace(self, session, seed_org_workspace):
        repo = JobRepo(session)
        for i in range(5):
            repo.create(Job(
                job_id=f"jlist{i}", workspace_id="ws1",
                user_id="u1", pipeline_type="test",
            ))
        session.flush()

        jobs = repo.list_by_workspace("ws1")
        assert len(jobs) >= 5

    def test_pending_jobs(self, session, seed_org_workspace):
        repo = JobRepo(session)
        repo.create(Job(
            job_id="jp1", workspace_id="ws1",
            user_id="u1", pipeline_type="test",
        ))
        repo.create(Job(
            job_id="jp2", workspace_id="ws1",
            user_id="u1", pipeline_type="test",
        ))
        session.flush()

        repo.update_status("jp1", JobStatus.RUNNING.value)
        session.flush()

        pending = repo.pending_jobs("ws1")
        pending_ids = [j.job_id for j in pending]
        assert "jp2" in pending_ids
        assert "jp1" not in pending_ids


# ═══════════════════════════════════════════════════════════════
# AGENT CONFIG REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestAgentConfigRepo:
    def test_create(self, session, seed_org_workspace):
        repo = AgentConfigRepo(session)
        config = repo.upsert(
            workspace_id="ws1",
            agent_name="egm_analyzer",
            config={
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-20250514",
                "temperature": 0.3,
                "tool_allowlist": ["irr_calculator", "sensitivity_matrix"],
            },
            changed_by="u1",
        )
        session.flush()

        assert config["agent_name"] == "egm_analyzer"
        assert config["model_provider"] == "anthropic"
        assert config["version"] == 1

    def test_update_creates_version(self, session, seed_org_workspace):
        repo = AgentConfigRepo(session)

        # Create initial
        repo.upsert(
            workspace_id="ws1", agent_name="deal_scorer",
            config={"temperature": 0.5, "model_name": "gpt-4o"},
            changed_by="u1",
        )
        session.flush()

        # Update
        updated = repo.upsert(
            workspace_id="ws1", agent_name="deal_scorer",
            config={"temperature": 0.2},
            changed_by="u1", change_reason="Lower temperature for consistency",
        )
        session.flush()

        assert updated["version"] == 2
        assert updated["temperature"] == 0.2

        # Check version history
        history = repo.get_version_history("ws1", "deal_scorer")
        assert len(history) == 1  # One snapshot of version 1
        assert history[0]["version"] == 1

    def test_rollback(self, session, seed_org_workspace):
        repo = AgentConfigRepo(session)

        # Create v1
        repo.upsert(
            workspace_id="ws1", agent_name="rollback_test",
            config={"temperature": 0.5, "model_name": "gpt-4o"},
            changed_by="u1",
        )
        session.flush()

        # Update to v2
        repo.upsert(
            workspace_id="ws1", agent_name="rollback_test",
            config={"temperature": 0.9, "model_name": "gpt-4o-mini"},
            changed_by="u1",
        )
        session.flush()

        # Rollback to v1
        rolled = repo.rollback("ws1", "rollback_test", 1, changed_by="u1")
        session.flush()

        assert rolled is not None
        assert rolled["version"] == 3  # Rollback creates a new version

    def test_list_by_workspace(self, session, seed_org_workspace):
        repo = AgentConfigRepo(session)
        repo.upsert("ws1", "agent_a", {"temperature": 0.1}, changed_by="u1")
        repo.upsert("ws1", "agent_b", {"temperature": 0.2}, changed_by="u1")
        session.flush()

        configs = repo.list_by_workspace("ws1")
        names = [c["agent_name"] for c in configs]
        assert "agent_a" in names
        assert "agent_b" in names


# ═══════════════════════════════════════════════════════════════
# TOOL RUN REPO TESTS
# ═══════════════════════════════════════════════════════════════

class TestToolRunRepo:
    def test_record_and_list(self, session):
        repo = ToolRunRepo(session)
        run_id = repo.record(
            workspace_id="ws1",
            tool_name="irr_calculator",
            inputs={"cash_flows": [-100000, 25000, 30000, 35000, 40000]},
            outputs={"irr": 0.1247, "npv_at_10pct": 5230.50},
            user_id="u1",
            execution_ms=12,
        )
        session.flush()

        assert run_id > 0

        runs = repo.list_runs("ws1", tool_name="irr_calculator")
        assert len(runs) >= 1
        assert runs[0]["outputs"]["irr"] == 0.1247

    def test_filter_by_tool(self, session):
        repo = ToolRunRepo(session)
        repo.record("ws1", "amortization", {"principal": 100000}, {"schedule": []})
        repo.record("ws1", "dscr", {"noi": 50000}, {"ratio": 1.25})
        repo.record("ws1", "amortization", {"principal": 200000}, {"schedule": []})
        session.flush()

        amort_runs = repo.list_runs("ws1", tool_name="amortization")
        assert len(amort_runs) == 2

        dscr_runs = repo.list_runs("ws1", tool_name="dscr")
        assert len(dscr_runs) == 1


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: FULL WORKFLOW
# ═══════════════════════════════════════════════════════════════

class TestFullWorkflow:
    """End-to-end test: create org → workspace → user → job → audit."""

    def test_complete_lifecycle(self, session):
        # 1. Create org
        org_repo = OrganizationRepo(session)
        org_repo.create(Organization(org_id="int_org", name="Integration Corp"))

        # 2. Create workspace
        ws_repo = WorkspaceRepo(session)
        ws_repo.create(Workspace(
            workspace_id="int_ws", org_id="int_org", name="Main"
        ))

        # 3. Create user with role
        user_repo = UserRepo(session)
        user_repo.create(UserIdentity(
            user_id="int_user", email="integration@test.com",
            name="Int User", org_id="int_org",
            workspace_roles={"int_ws": "operator"},
        ))

        # 4. Submit a job
        job_repo = JobRepo(session)
        job = Job(
            job_id="int_job", workspace_id="int_ws",
            user_id="int_user", pipeline_type="egm_analysis",
            config={"address": "123 Main St", "venue_type": "bar"},
        )
        job_repo.create(job)

        # 5. Log audit entry
        audit_repo = AuditLogRepo(session)
        audit_repo.append(
            workspace_id="int_ws", action="job.submit",
            resource="job:int_job", outcome="success",
            user_id="int_user",
        )

        # 6. Update job to running
        job_repo.update_status("int_job", JobStatus.RUNNING.value)

        # 7. Record a tool run
        tool_repo = ToolRunRepo(session)
        tool_repo.record(
            workspace_id="int_ws", tool_name="cap_rate",
            inputs={"noi": 75000, "value": 1000000},
            outputs={"cap_rate": 0.075},
            user_id="int_user", execution_ms=3,
        )

        # 8. Complete the job
        job_repo.update_status(
            "int_job", JobStatus.COMPLETED.value,
            result={"recommendation": "GO", "irr": 0.22},
        )

        session.flush()

        # ── Verify everything ────────────────────────────────

        org = org_repo.get("int_org")
        assert org.name == "Integration Corp"

        ws = ws_repo.get("int_ws")
        assert ws.org_id == "int_org"

        user = user_repo.get("int_user")
        assert user.workspace_roles["int_ws"] == "operator"

        final_job = job_repo.get("int_job")
        assert final_job.status == "completed"
        assert final_job.result["irr"] == 0.22

        audit_entries = audit_repo.list_entries("int_ws")
        assert len(audit_entries) >= 1

        tool_runs = tool_repo.list_runs("int_ws")
        assert len(tool_runs) >= 1
        assert tool_runs[0]["tool_name"] == "cap_rate"
