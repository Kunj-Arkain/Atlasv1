"""
Phase 3 Test Suite — Workers, Tenants/RBAC, Eval Harness
Run: cd /home/claude/phase1 && python -m unittest tests.test_phase3 -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# SUBPROCESS WORKERS
# ═══════════════════════════════════════════════════════════════

class TestSubprocessWorker(unittest.TestCase):

    def test_successful_execution(self):
        from engine.workers import SubprocessWorker, ResourceQuota
        worker = SubprocessWorker(ResourceQuota(wall_time_seconds=10))
        result = worker.run(lambda: 42, stage_name="test")
        self.assertTrue(result.succeeded)
        self.assertEqual(result.output, 42)
        self.assertEqual(result.status, "completed")
        self.assertGreater(result.wall_time_ms, 0)

    def test_exception_captured(self):
        from engine.workers import SubprocessWorker, ResourceQuota
        def bad():
            raise ValueError("deliberate error")
        worker = SubprocessWorker(ResourceQuota(wall_time_seconds=10))
        result = worker.run(bad, stage_name="fail_test")
        self.assertFalse(result.succeeded)
        self.assertEqual(result.status, "failed")
        self.assertIn("ValueError", result.error)
        self.assertIn("deliberate error", result.error)

    def test_timeout_kills_process(self):
        from engine.workers import SubprocessWorker, ResourceQuota
        import time as _time
        def slow():
            _time.sleep(60)
            return "should not reach"
        worker = SubprocessWorker(ResourceQuota(wall_time_seconds=1))
        result = worker.run(slow, stage_name="timeout_test")
        self.assertFalse(result.succeeded)
        self.assertEqual(result.status, "timeout")
        self.assertIn("exceeded", result.error.lower())

    def test_return_complex_data(self):
        from engine.workers import SubprocessWorker, ResourceQuota
        def compute():
            return {"revenue": 1_000_000, "agents": ["a", "b"]}
        worker = SubprocessWorker(ResourceQuota(wall_time_seconds=10))
        result = worker.run(compute)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.output["revenue"], 1_000_000)
        self.assertEqual(len(result.output["agents"]), 2)

    def test_worker_result_to_dict(self):
        from engine.workers import WorkerResult
        r = WorkerResult(status="completed", stage_name="s1", pid=1234, wall_time_ms=500)
        d = r.to_dict()
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["stage_name"], "s1")
        self.assertEqual(d["pid"], 1234)


class TestRunInSubprocess(unittest.TestCase):

    def test_drop_in_replacement(self):
        from engine.workers import run_in_subprocess
        result = run_in_subprocess(lambda: "hello", timeout_seconds=10)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.output, "hello")


class TestWorkerPool(unittest.TestCase):

    def test_batch_execution(self):
        from engine.workers import WorkerPool, WorkItem, ResourceQuota
        pool = WorkerPool(max_workers=2, quota=ResourceQuota(wall_time_seconds=10))
        items = [
            WorkItem(fn=lambda: "a", stage_name="s1"),
            WorkItem(fn=lambda: "b", stage_name="s2"),
            WorkItem(fn=lambda: "c", stage_name="s3"),
        ]
        results = pool.execute_batch(items)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.succeeded for r in results))
        outputs = {r.output for r in results}
        self.assertEqual(outputs, {"a", "b", "c"})

    def test_batch_exceeds_queue_size(self):
        from engine.workers import WorkerPool, WorkItem
        pool = WorkerPool(max_queue_size=2)
        items = [WorkItem(fn=lambda: "x") for _ in range(5)]
        with self.assertRaises(RuntimeError):
            pool.execute_batch(items)

    def test_execute_single(self):
        from engine.workers import WorkerPool, ResourceQuota
        pool = WorkerPool(quota=ResourceQuota(wall_time_seconds=10))
        result = pool.execute_single(lambda: 99, stage_name="single")
        self.assertTrue(result.succeeded)
        self.assertEqual(result.output, 99)


# ═══════════════════════════════════════════════════════════════
# RBAC
# ═══════════════════════════════════════════════════════════════

class TestRBAC(unittest.TestCase):

    def _setup_authz(self):
        from engine.tenants import AuthzEngine, UserIdentity, Role
        authz = AuthzEngine()
        authz.register_user(UserIdentity(
            user_id="owner1", email="owner@co.com",
            workspace_roles={"ws1": Role.OWNER.value},
        ))
        authz.register_user(UserIdentity(
            user_id="viewer1", email="viewer@co.com",
            workspace_roles={"ws1": Role.VIEWER.value},
        ))
        authz.register_user(UserIdentity(
            user_id="operator1", email="op@co.com",
            workspace_roles={"ws1": Role.OPERATOR.value},
        ))
        return authz

    def test_owner_has_all_permissions(self):
        from engine.tenants import Permission
        authz = self._setup_authz()
        for perm in Permission:
            self.assertTrue(authz.check("owner1", "ws1", perm.value),
                            f"Owner should have {perm.value}")

    def test_viewer_limited(self):
        from engine.tenants import Permission
        authz = self._setup_authz()
        self.assertTrue(authz.check("viewer1", "ws1", Permission.JOB_VIEW.value))
        self.assertTrue(authz.check("viewer1", "ws1", Permission.BILLING_VIEW.value))
        self.assertFalse(authz.check("viewer1", "ws1", Permission.PIPELINE_RUN.value))
        self.assertFalse(authz.check("viewer1", "ws1", Permission.SECRET_CREATE.value))

    def test_operator_can_run_not_create(self):
        from engine.tenants import Permission
        authz = self._setup_authz()
        self.assertTrue(authz.check("operator1", "ws1", Permission.PIPELINE_RUN.value))
        self.assertFalse(authz.check("operator1", "ws1", Permission.PIPELINE_CREATE.value))

    def test_require_raises_on_denied(self):
        from engine.tenants import Permission, AuthorizationError
        authz = self._setup_authz()
        with self.assertRaises(AuthorizationError) as ctx:
            authz.require("viewer1", "ws1", Permission.PIPELINE_RUN.value)
        self.assertIn("viewer1", str(ctx.exception))
        self.assertIn("pipeline.run", str(ctx.exception))

    def test_unknown_user_denied(self):
        from engine.tenants import Permission
        authz = self._setup_authz()
        self.assertFalse(authz.check("ghost", "ws1", Permission.JOB_VIEW.value))

    def test_wrong_workspace_denied(self):
        from engine.tenants import Permission
        authz = self._setup_authz()
        self.assertFalse(authz.check("owner1", "ws999", Permission.JOB_VIEW.value))

    def test_grant_role(self):
        from engine.tenants import Permission, Role
        authz = self._setup_authz()
        authz.grant_role("viewer1", "ws1", Role.ADMIN.value)
        self.assertTrue(authz.check("viewer1", "ws1", Permission.PIPELINE_CREATE.value))


# ═══════════════════════════════════════════════════════════════
# SECRETS VAULT
# ═══════════════════════════════════════════════════════════════

class TestSecretsVault(unittest.TestCase):

    def test_store_and_retrieve(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="test-key-123")
        vault.set_secret("ws1", "OPENAI_API_KEY", "sk-abc123")
        vault.set_secret("ws1", "DB_URL", "postgres://localhost/db")
        self.assertEqual(vault.get_secret("ws1", "OPENAI_API_KEY"), "sk-abc123")
        self.assertEqual(vault.get_secret("ws1", "DB_URL"), "postgres://localhost/db")

    def test_workspace_isolation(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="test-key-123")
        vault.set_secret("ws1", "KEY", "value1")
        vault.set_secret("ws2", "KEY", "value2")
        self.assertEqual(vault.get_secret("ws1", "KEY"), "value1")
        self.assertEqual(vault.get_secret("ws2", "KEY"), "value2")
        self.assertIsNone(vault.get_secret("ws3", "KEY"))

    def test_delete_secret(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="k")
        vault.set_secret("ws1", "X", "y")
        self.assertTrue(vault.delete_secret("ws1", "X"))
        self.assertIsNone(vault.get_secret("ws1", "X"))

    def test_list_keys(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="k")
        vault.set_secret("ws1", "A", "1")
        vault.set_secret("ws1", "B", "2")
        keys = vault.list_keys("ws1")
        self.assertEqual(set(keys), {"A", "B"})

    def test_ephemeral_env(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="k")
        vault.set_secret("ws1", "API_KEY", "secret123")
        vault.set_secret("ws1", "DB", "pg://x")
        env = vault.ephemeral_env("ws1", ["API_KEY", "DB", "MISSING"])
        self.assertEqual(env["API_KEY"], "secret123")
        self.assertEqual(env["DB"], "pg://x")
        self.assertNotIn("MISSING", env)

    def test_persistence(self):
        from engine.tenants import SecretsVault
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "secrets.json"
            v1 = SecretsVault(master_key="k", storage_path=path)
            v1.set_secret("ws1", "X", "hello")
            # Load from disk
            v2 = SecretsVault(master_key="k", storage_path=path)
            self.assertEqual(v2.get_secret("ws1", "X"), "hello")


# ═══════════════════════════════════════════════════════════════
# QUOTAS
# ═══════════════════════════════════════════════════════════════

class TestQuotaEnforcer(unittest.TestCase):

    def test_within_quota_passes(self):
        from engine.tenants import QuotaEnforcer, TenantQuota
        qe = QuotaEnforcer()
        qe.set_quota("ws1", TenantQuota(max_concurrent_jobs=5))
        self.assertTrue(qe.check_can_run("ws1"))

    def test_concurrent_limit(self):
        from engine.tenants import QuotaEnforcer, TenantQuota, QuotaExceededError
        qe = QuotaEnforcer()
        qe.set_quota("ws1", TenantQuota(max_concurrent_jobs=2))
        qe.job_started("ws1")
        qe.job_started("ws1")
        with self.assertRaises(QuotaExceededError) as ctx:
            qe.check_can_run("ws1")
        self.assertIn("concurrent_jobs", str(ctx.exception))

    def test_cost_ceiling(self):
        from engine.tenants import QuotaEnforcer, TenantQuota, QuotaExceededError
        qe = QuotaEnforcer()
        qe.set_quota("ws1", TenantQuota(monthly_cost_ceiling_usd=10.0))
        qe.add_spend("ws1", 15.0)
        with self.assertRaises(QuotaExceededError) as ctx:
            qe.check_can_run("ws1")
        self.assertIn("monthly_cost", str(ctx.exception))

    def test_agent_limit(self):
        from engine.tenants import QuotaEnforcer, TenantQuota, QuotaExceededError
        qe = QuotaEnforcer()
        qe.set_quota("ws1", TenantQuota(max_agents_per_pipeline=3))
        with self.assertRaises(QuotaExceededError):
            qe.check_can_run("ws1", num_agents=5)

    def test_llm_tier_restriction(self):
        from engine.tenants import QuotaEnforcer, TenantQuota, QuotaExceededError
        qe = QuotaEnforcer()
        qe.set_quota("ws1", TenantQuota(allowed_llm_tiers=["light"]))
        with self.assertRaises(QuotaExceededError):
            qe.check_can_run("ws1", llm_tier="premium")

    def test_job_finished_decrements(self):
        from engine.tenants import QuotaEnforcer, TenantQuota
        qe = QuotaEnforcer()
        qe.set_quota("ws1", TenantQuota(max_concurrent_jobs=1))
        qe.job_started("ws1")
        qe.job_finished("ws1")
        self.assertTrue(qe.check_can_run("ws1"))


# ═══════════════════════════════════════════════════════════════
# JOB QUEUE
# ═══════════════════════════════════════════════════════════════

class TestJobQueue(unittest.TestCase):

    def test_submit_and_get(self):
        from engine.tenants import JobQueue
        q = JobQueue()
        job = q.submit("ws1", "u1", "real_estate", {"address": "123 Main"})
        self.assertIsNotNone(job.job_id)
        self.assertEqual(job.status, "queued")
        fetched = q.get(job.job_id)
        self.assertEqual(fetched.workspace_id, "ws1")

    def test_list_by_workspace(self):
        from engine.tenants import JobQueue
        q = JobQueue()
        q.submit("ws1", "u1", "pipe_a")
        q.submit("ws1", "u1", "pipe_b")
        q.submit("ws2", "u2", "pipe_c")
        ws1_jobs = q.list_jobs("ws1")
        self.assertEqual(len(ws1_jobs), 2)

    def test_update_status(self):
        from engine.tenants import JobQueue
        q = JobQueue()
        job = q.submit("ws1", "u1", "p")
        q.update_status(job.job_id, "running")
        self.assertEqual(q.get(job.job_id).status, "running")
        self.assertNotEqual(q.get(job.job_id).started_at, "")
        q.update_status(job.job_id, "completed", result={"ok": True})
        self.assertEqual(q.get(job.job_id).status, "completed")

    def test_cancel(self):
        from engine.tenants import JobQueue
        q = JobQueue()
        job = q.submit("ws1", "u1", "p")
        self.assertTrue(q.cancel(job.job_id))
        self.assertEqual(q.get(job.job_id).status, "cancelled")
        self.assertFalse(q.cancel(job.job_id))  # Can't cancel twice


# ═══════════════════════════════════════════════════════════════
# EVAL SUITE
# ═══════════════════════════════════════════════════════════════

class TestEvalSuite(unittest.TestCase):

    def test_basic_passing_suite(self):
        from engine.eval import EvalSuite, EvalCase, EvalCategory, Assertion
        suite = EvalSuite("basic")
        suite.add_case(EvalCase(
            name="has_revenue",
            category=EvalCategory.ACCURACY.value,
            assertions=[
                Assertion(type="contains", expected="revenue"),
                Assertion(type="not_contains", expected="ERROR"),
            ],
        ))
        results = suite.run(lambda inp: "revenue projection: $5M")
        self.assertEqual(results.total, 1)
        self.assertEqual(results.passed, 1)
        self.assertEqual(results.failed, 0)

    def test_failing_suite(self):
        from engine.eval import EvalSuite, EvalCase, EvalCategory, Assertion
        suite = EvalSuite("fail")
        suite.add_case(EvalCase(
            name="needs_revenue",
            category=EvalCategory.ACCURACY.value,
            assertions=[Assertion(type="contains", expected="revenue")],
        ))
        results = suite.run(lambda inp: "no useful output")
        self.assertEqual(results.failed, 1)
        self.assertFalse(results.results[0].passed)

    def test_error_handling(self):
        from engine.eval import EvalSuite, EvalCase, EvalCategory, Assertion
        suite = EvalSuite("err")
        suite.add_case(EvalCase(
            name="crashes",
            category=EvalCategory.ACCURACY.value,
            assertions=[Assertion(type="contains", expected="x")],
        ))
        def bad_runner(inp):
            raise RuntimeError("boom")
        results = suite.run(bad_runner)
        self.assertEqual(results.errors, 1)
        self.assertIn("RuntimeError", results.results[0].error)

    def test_suite_summary(self):
        from engine.eval import EvalSuite, EvalCase, EvalCategory, Assertion
        suite = EvalSuite("mixed")
        suite.add_case(EvalCase(
            name="pass1", category=EvalCategory.ACCURACY.value,
            assertions=[Assertion(type="contains", expected="ok")],
        ))
        suite.add_case(EvalCase(
            name="fail1", category=EvalCategory.SECURITY.value,
            assertions=[Assertion(type="contains", expected="missing")],
        ))
        results = suite.run(lambda inp: "ok result")
        s = results.summary()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["passed"], 1)
        self.assertEqual(s["failed"], 1)
        self.assertIn("accuracy", s["by_category"])
        self.assertIn("security", s["by_category"])

    def test_multiple_assertions(self):
        from engine.eval import EvalSuite, EvalCase, EvalCategory, Assertion
        suite = EvalSuite("multi")
        suite.add_case(EvalCase(
            name="multi_check",
            category=EvalCategory.ACCURACY.value,
            assertions=[
                Assertion(type="contains", expected="revenue"),
                Assertion(type="not_contains", expected="ERROR"),
                Assertion(type="contains", expected="$"),
            ],
        ))
        results = suite.run(lambda inp: "revenue: $5M projection")
        self.assertEqual(results.passed, 1)


# ═══════════════════════════════════════════════════════════════
# ASSERTION ENGINE
# ═══════════════════════════════════════════════════════════════

class TestAssertionEngine(unittest.TestCase):

    def test_contains(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="contains", expected="hello"), "hello world")
        self.assertTrue(r["passed"])

    def test_not_contains(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="not_contains", expected="secret"), "clean output")
        self.assertTrue(r["passed"])

    def test_equals(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="equals", expected=42), 42)
        self.assertTrue(r["passed"])
        r2 = check_assertion(Assertion(type="equals", expected=42), 43)
        self.assertFalse(r2["passed"])

    def test_greater_than(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="greater_than", expected=0.5), 0.9)
        self.assertTrue(r["passed"])

    def test_less_than(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="less_than", expected=100), 50)
        self.assertTrue(r["passed"])

    def test_regex_match(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="regex_match", expected=r"\d{3}-\d{4}"), "Call 555-1234")
        self.assertTrue(r["passed"])

    def test_field_path_resolution(self):
        from engine.eval import Assertion, check_assertion
        data = {"report": {"sections": ["intro", "analysis", "conclusion"]}}
        r = check_assertion(
            Assertion(type="is_not_none", expected=None, field="report.sections"),
            data
        )
        self.assertTrue(r["passed"])

    def test_is_true_false(self):
        from engine.eval import Assertion, check_assertion
        r1 = check_assertion(Assertion(type="is_true", expected=None), True)
        self.assertTrue(r1["passed"])
        r2 = check_assertion(Assertion(type="is_false", expected=None), False)
        self.assertTrue(r2["passed"])

    def test_length_gte(self):
        from engine.eval import Assertion, check_assertion
        r = check_assertion(Assertion(type="length_gte", expected=3), [1, 2, 3, 4])
        self.assertTrue(r["passed"])


# ═══════════════════════════════════════════════════════════════
# SECURITY TEST BATTERIES
# ═══════════════════════════════════════════════════════════════

class TestSecurityBatteries(unittest.TestCase):

    def test_prompt_injection_cases_exist(self):
        from engine.eval import prompt_injection_test_cases
        cases = prompt_injection_test_cases()
        self.assertGreaterEqual(len(cases), 5)
        for c in cases:
            self.assertEqual(c.category, "prompt_injection")
            self.assertGreater(len(c.assertions), 0)

    def test_tool_policy_cases_exist(self):
        from engine.eval import tool_policy_test_cases
        cases = tool_policy_test_cases()
        self.assertGreaterEqual(len(cases), 3)

    def test_contract_cases_exist(self):
        from engine.eval import contract_test_cases
        cases = contract_test_cases()
        self.assertGreaterEqual(len(cases), 2)

    def test_injection_tests_run(self):
        """Run injection battery against a 'safe' handler that blocks everything."""
        from engine.eval import EvalSuite, prompt_injection_test_cases
        suite = EvalSuite("injection_test")
        suite.add_cases(prompt_injection_test_cases())

        def safe_handler(inp):
            return "I cannot help with that request. Please contact support."

        results = suite.run(safe_handler)
        # All should pass since safe handler doesn't leak anything
        self.assertEqual(results.passed, results.total)


# ═══════════════════════════════════════════════════════════════
# RELEASE GATE
# ═══════════════════════════════════════════════════════════════

class TestReleaseGate(unittest.TestCase):

    def _make_suite_result(self, cases):
        """Helper: build EvalSuiteResult from list of (name, category, passed) tuples."""
        from engine.eval import EvalSuiteResult, EvalResult
        sr = EvalSuiteResult(suite_name="test")
        for name, cat, passed in cases:
            sr.total += 1
            if passed:
                sr.passed += 1
            else:
                sr.failed += 1
            sr.results.append(EvalResult(case_name=name, category=cat, passed=passed))
        return sr

    def test_all_pass_approved(self):
        from engine.eval import ReleaseGate, ReleaseGateConfig, EvalCategory
        gate = ReleaseGate(ReleaseGateConfig(
            required_categories=[EvalCategory.ACCURACY.value],
        ))
        sr = self._make_suite_result([
            ("t1", EvalCategory.ACCURACY.value, True),
            ("t2", EvalCategory.ACCURACY.value, True),
            ("t3", EvalCategory.SECURITY.value, True),
        ])
        decision = gate.evaluate(sr)
        self.assertTrue(decision.approved)

    def test_low_pass_rate_blocked(self):
        from engine.eval import ReleaseGate, ReleaseGateConfig, EvalCategory
        gate = ReleaseGate(ReleaseGateConfig(min_pass_rate=0.9))
        sr = self._make_suite_result([
            ("t1", EvalCategory.ACCURACY.value, True),
            ("t2", EvalCategory.ACCURACY.value, False),
            ("t3", EvalCategory.ACCURACY.value, False),
        ])
        decision = gate.evaluate(sr)
        self.assertFalse(decision.approved)
        self.assertTrue(any("Pass rate" in r for r in decision.reasons))

    def test_security_zero_tolerance(self):
        from engine.eval import ReleaseGate, ReleaseGateConfig, EvalCategory
        gate = ReleaseGate(ReleaseGateConfig(
            security_zero_tolerance=True,
            required_categories=[],
        ))
        sr = self._make_suite_result([
            ("t1", EvalCategory.ACCURACY.value, True),
            ("t2", EvalCategory.ACCURACY.value, True),
            ("t3", EvalCategory.PROMPT_INJECTION.value, True),
            ("t4", EvalCategory.PROMPT_INJECTION.value, False),  # Security fail
        ])
        decision = gate.evaluate(sr)
        self.assertFalse(decision.approved)
        self.assertTrue(any("zero-tolerance" in r for r in decision.reasons))

    def test_missing_required_category_blocked(self):
        from engine.eval import ReleaseGate, ReleaseGateConfig, EvalCategory
        gate = ReleaseGate(ReleaseGateConfig(
            required_categories=[EvalCategory.SECURITY.value],
        ))
        sr = self._make_suite_result([
            ("t1", EvalCategory.ACCURACY.value, True),
        ])
        decision = gate.evaluate(sr)
        self.assertFalse(decision.approved)
        self.assertTrue(any("Required category" in r for r in decision.reasons))

    def test_category_min_pass_rate(self):
        from engine.eval import ReleaseGate, ReleaseGateConfig, EvalCategory
        gate = ReleaseGate(ReleaseGateConfig(
            min_pass_rate=0.5,
            required_categories=[],
            min_category_pass_rates={EvalCategory.ACCURACY.value: 0.8},
        ))
        sr = self._make_suite_result([
            ("a1", EvalCategory.ACCURACY.value, True),
            ("a2", EvalCategory.ACCURACY.value, False),
            ("a3", EvalCategory.ACCURACY.value, False),
        ])
        decision = gate.evaluate(sr)
        self.assertFalse(decision.approved)
        self.assertTrue(any("accuracy" in r for r in decision.reasons))

    def test_decision_has_summary(self):
        from engine.eval import ReleaseGate, EvalCategory
        gate = ReleaseGate()
        sr = self._make_suite_result([
            ("t1", EvalCategory.ACCURACY.value, True),
            ("t2", EvalCategory.SECURITY.value, True),
        ])
        decision = gate.evaluate(sr)
        self.assertIn("suite", decision.suite_summary)
        self.assertIn("total", decision.suite_summary)


if __name__ == "__main__":
    unittest.main()
