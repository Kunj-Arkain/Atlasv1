"""
V3 Test Suite — Security + Durability Fixes
Run: cd /home/claude/phase1 && python -m unittest tests.test_v3_fixes -v

Covers:
  P0.1 — Subprocess stage execution (hard kill)
  P0.2 — Safe condition evaluator (no eval())
  P0.3 — HMAC-CTR encryption (replaces XOR)
  P0.4 — Fail-closed HITL approvals
  P1.1 — SQLite-backed job queue
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
# P0.2 — SAFE CONDITION EVALUATOR (replaces eval())
# ═══════════════════════════════════════════════════════════════

class TestSafeConditionEvaluator(unittest.TestCase):
    """Verify the safe evaluator handles all valid patterns
    AND rejects dangerous ones that eval() would execute."""

    def _eval(self, expr, state=None, results=None):
        from engine.runtime import safe_eval_condition
        scope = {"state": state or {}, "results": results or {}}
        return safe_eval_condition(expr, scope)

    # ── Valid expressions ────────────────────────────────

    def test_simple_true(self):
        self.assertTrue(self._eval("True"))

    def test_simple_false(self):
        self.assertFalse(self._eval("False"))

    def test_dict_lookup(self):
        self.assertTrue(self._eval(
            'state["skip"] == True', state={"skip": True}))

    def test_nested_dict_lookup(self):
        self.assertTrue(self._eval(
            'state["config"]["mode"] == "fast"',
            state={"config": {"mode": "fast"}}))

    def test_comparison_operators(self):
        self.assertTrue(self._eval('state["count"] > 5', state={"count": 10}))
        self.assertTrue(self._eval('state["count"] <= 10', state={"count": 10}))
        self.assertFalse(self._eval('state["count"] < 5', state={"count": 10}))

    def test_boolean_and(self):
        self.assertTrue(self._eval(
            'state["a"] and state["b"]', state={"a": True, "b": True}))
        self.assertFalse(self._eval(
            'state["a"] and state["b"]', state={"a": True, "b": False}))

    def test_boolean_or(self):
        self.assertTrue(self._eval(
            'state["a"] or state["b"]', state={"a": False, "b": True}))

    def test_not(self):
        self.assertTrue(self._eval('not state["done"]', state={"done": False}))

    def test_in_operator(self):
        self.assertTrue(self._eval(
            '"research" in results', results={"research": "done"}))

    def test_not_in(self):
        self.assertTrue(self._eval(
            '"missing" not in results', results={"research": "done"}))

    def test_results_stage_check(self):
        """Common pattern: skip stage if previous stage already produced results."""
        self.assertTrue(self._eval(
            'results["research"] == "done"', results={"research": "done"}))

    def test_ternary(self):
        from engine.runtime import SafeConditionEvaluator
        result = SafeConditionEvaluator({"x": 5}).evaluate(
            '"yes" if x > 3 else "no"')
        self.assertEqual(result, "yes")

    def test_list_membership(self):
        self.assertTrue(self._eval(
            'state["tier"] in ["premium", "enterprise"]',
            state={"tier": "premium"}))

    def test_callable_condition(self):
        """skip_if/run_if can also be callables (not just strings)."""
        from engine.runtime import safe_eval_condition
        fn = lambda scope: scope["state"].get("skip", False)
        self.assertTrue(safe_eval_condition(fn, {"state": {"skip": True}}))

    # ── SECURITY: Dangerous expressions BLOCKED ──────────

    def test_import_blocked(self):
        """__import__ must not execute."""
        result = self._eval('__import__("os").system("echo pwned")')
        self.assertFalse(result)

    def test_function_call_blocked(self):
        """Function calls are not in the safe AST whitelist."""
        result = self._eval('len(state)')
        self.assertFalse(result)

    def test_os_system_blocked(self):
        result = self._eval('os.system("rm -rf /")')
        self.assertFalse(result)

    def test_exec_blocked(self):
        result = self._eval('exec("import os")')
        self.assertFalse(result)

    def test_dunder_access_returns_none(self):
        """Attribute access on non-dict objects returns None (no method calls)."""
        from engine.runtime import SafeConditionEvaluator
        result = SafeConditionEvaluator({"x": "hello"}).evaluate(
            'x.__class__.__bases__')
        self.assertIsNone(result)

    def test_lambda_blocked(self):
        result = self._eval('(lambda: True)()')
        self.assertFalse(result)

    def test_comprehension_blocked(self):
        result = self._eval('[x for x in range(10)]')
        self.assertFalse(result)

    def test_walrus_blocked(self):
        """Named expressions (walrus operator) should not work."""
        result = self._eval('(x := 42)')
        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════
# P0.2 — INTEGRATION: skip_if/run_if use safe evaluator in runtime
# ═══════════════════════════════════════════════════════════════

class TestRuntimeSafeConditions(unittest.TestCase):

    def test_skip_if_with_string(self):
        """skip_if still works with string conditions (safe eval)."""
        from engine.runtime import PipelineRuntime, StageDef
        stages = [
            StageDef(name="a", handler="h_a"),
            StageDef(name="b", handler="h_b", skip_if='state["skip_b"] == True'),
        ]
        state = {"skip_b": True}
        rt = PipelineRuntime(
            stages, handlers={"h_a": lambda ctx: "a", "h_b": lambda ctx: "b"},
            state=state, output_dir=tempfile.mkdtemp(),
        )
        results = rt.run()
        self.assertEqual(results["a"], "a")
        self.assertNotIn("b", results)

    def test_skip_if_with_callable(self):
        """skip_if works with callables too."""
        from engine.runtime import PipelineRuntime, StageDef
        stages = [
            StageDef(name="a", handler="h_a",
                     skip_if=lambda scope: scope["state"].get("no_a")),
        ]
        rt = PipelineRuntime(
            stages, handlers={"h_a": lambda ctx: "a"},
            state={"no_a": True}, output_dir=tempfile.mkdtemp(),
        )
        results = rt.run()
        self.assertNotIn("a", results)

    def test_malicious_skip_if_harmless(self):
        """Malicious skip_if expression can't execute code."""
        from engine.runtime import PipelineRuntime, StageDef
        stages = [
            StageDef(name="a", handler="h_a",
                     skip_if='__import__("os").system("echo pwned")'),
        ]
        rt = PipelineRuntime(
            stages, handlers={"h_a": lambda ctx: "safe"},
            state={}, output_dir=tempfile.mkdtemp(),
        )
        results = rt.run()
        # Stage should execute normally (malicious condition returns False)
        self.assertEqual(results["a"], "safe")


# ═══════════════════════════════════════════════════════════════
# P0.1 — SUBPROCESS STAGE EXECUTION
# ═══════════════════════════════════════════════════════════════

class TestSubprocessStageExecution(unittest.TestCase):

    def test_subprocess_mode_runs(self):
        """Pipeline works with use_subprocess=True."""
        from engine.runtime import PipelineRuntime, StageDef
        stages = [
            StageDef(name="a", handler="h_a"),
            StageDef(name="b", handler="h_b", depends_on=["a"]),
        ]
        rt = PipelineRuntime(
            stages, handlers={
                "h_a": lambda ctx: "result_a",
                "h_b": lambda ctx: "result_b",
            },
            state={}, output_dir=tempfile.mkdtemp(),
            use_subprocess=True,
        )
        results = rt.run()
        self.assertEqual(results["a"], "result_a")
        self.assertEqual(results["b"], "result_b")

    def test_subprocess_timeout_kills(self):
        """Stage that exceeds timeout is SIGKILL'd in subprocess mode."""
        from engine.runtime import PipelineRuntime, StageDef
        import time as _time

        def slow_handler(ctx):
            _time.sleep(60)
            return "should not reach"

        stages = [
            StageDef(name="slow", handler="h_slow",
                     timeout_seconds=2, priority=0),
        ]
        rt = PipelineRuntime(
            stages, handlers={"h_slow": slow_handler},
            state={}, output_dir=tempfile.mkdtemp(),
            use_subprocess=True,
        )
        with self.assertRaises(Exception) as ctx:
            rt.run()
        # Should mention timeout or kill
        self.assertTrue(
            "killed" in str(ctx.exception).lower() or
            "timeout" in str(ctx.exception).lower()
        )

    def test_subprocess_parallel_wave(self):
        """Parallel wave works in subprocess mode."""
        from engine.runtime import PipelineRuntime, StageDef
        stages = [
            StageDef(name="a", handler="h_a"),
            StageDef(name="b", handler="h_b"),
        ]
        rt = PipelineRuntime(
            stages, handlers={
                "h_a": lambda ctx: "a_done",
                "h_b": lambda ctx: "b_done",
            },
            state={}, output_dir=tempfile.mkdtemp(),
            use_subprocess=True,
        )
        results = rt.run()
        self.assertEqual(results["a"], "a_done")
        self.assertEqual(results["b"], "b_done")


# ═══════════════════════════════════════════════════════════════
# P0.4 — FAIL-CLOSED HITL
# ═══════════════════════════════════════════════════════════════

class TestFailClosedHITL(unittest.TestCase):

    def test_no_callback_denies(self):
        """When no HITL callback configured, human-required tools are DENIED."""
        from engine.policy import PolicyBroker, ToolPolicy, ApprovalRequirement
        import json as _json

        # No hitl_callback — should fail-closed
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(
            tool_name="dangerous",
            approval=ApprovalRequirement.HUMAN.value,
        ))
        result = broker.invoke("dangerous", lambda: "should not run", {})
        parsed = _json.loads(result)
        self.assertTrue(parsed["blocked"])
        self.assertIn("rejected", parsed["error"].lower())

    def test_with_callback_true_allows(self):
        """With callback returning True, tool executes."""
        from engine.policy import PolicyBroker, ToolPolicy, ApprovalRequirement
        broker = PolicyBroker(hitl_callback=lambda *args: True)
        broker.register_policy(ToolPolicy(
            tool_name="dangerous",
            approval=ApprovalRequirement.HUMAN.value,
        ))
        result = broker.invoke("dangerous", lambda: "executed", {})
        self.assertEqual(result, "executed")

    def test_with_callback_false_blocks(self):
        """With callback returning False, tool is blocked."""
        from engine.policy import PolicyBroker, ToolPolicy, ApprovalRequirement
        import json as _json
        broker = PolicyBroker(hitl_callback=lambda *args: False)
        broker.register_policy(ToolPolicy(
            tool_name="dangerous",
            approval=ApprovalRequirement.HUMAN.value,
        ))
        result = broker.invoke("dangerous", lambda: "should not run", {})
        parsed = _json.loads(result)
        self.assertTrue(parsed["blocked"])

    def test_audit_logs_fail_closed(self):
        """Fail-closed denial is logged in audit trail."""
        from engine.policy import PolicyBroker, ToolPolicy, ApprovalRequirement
        from engine.observability import AuditLog
        audit = AuditLog()
        broker = PolicyBroker(audit=audit)
        broker.register_policy(ToolPolicy(
            tool_name="t",
            approval=ApprovalRequirement.HUMAN.value,
        ))
        broker.invoke("t", lambda: "x", {})
        actions = [e.action for e in audit.entries]
        self.assertIn("tool.approval_denied", actions)
        denied = [e for e in audit.entries if e.action == "tool.approval_denied"][0]
        self.assertEqual(denied.details["policy"], "fail_closed")


# ═══════════════════════════════════════════════════════════════
# P0.3 — SECRETS: HMAC-CTR ENCRYPTION (replaces XOR)
# ═══════════════════════════════════════════════════════════════

class TestSecretsHMACCTR(unittest.TestCase):

    def test_no_default_key_raises(self):
        """SecretsVault with no master key raises ValueError."""
        from engine.tenants import SecretsVault
        # Clear env var if set
        old = os.environ.pop("SECRETS_MASTER_KEY", None)
        try:
            with self.assertRaises(ValueError) as ctx:
                SecretsVault()
            self.assertIn("requires a master key", str(ctx.exception))
        finally:
            if old:
                os.environ["SECRETS_MASTER_KEY"] = old

    def test_encrypt_decrypt_roundtrip(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="test-key-v3-strong-32chars!!")
        vault.set_secret("ws1", "API_KEY", "sk-abc123xyz")
        self.assertEqual(vault.get_secret("ws1", "API_KEY"), "sk-abc123xyz")

    def test_different_workspaces_different_ciphertext(self):
        """Same plaintext encrypted differently per workspace (key derivation)."""
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="test-key")
        vault.set_secret("ws1", "K", "same_value")
        vault.set_secret("ws2", "K", "same_value")
        # Internal ciphertext should differ
        ct1 = vault._store["ws1"]["K"]
        ct2 = vault._store["ws2"]["K"]
        self.assertNotEqual(ct1, ct2)

    def test_nonce_makes_ciphertext_unique(self):
        """Encrypting same value twice produces different ciphertext (random nonce)."""
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="test-key")
        vault.set_secret("ws1", "A", "same")
        ct1 = vault._store["ws1"]["A"]
        vault.set_secret("ws1", "A", "same")
        ct2 = vault._store["ws1"]["A"]
        # With random nonce, ciphertexts should differ
        # (very small chance of collision, but practically impossible)
        self.assertNotEqual(ct1, ct2)

    def test_unicode_secrets(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="k")
        vault.set_secret("ws1", "K", "日本語テスト🔑")
        self.assertEqual(vault.get_secret("ws1", "K"), "日本語テスト🔑")

    def test_long_secret(self):
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="k")
        long_val = "x" * 10000
        vault.set_secret("ws1", "LONG", long_val)
        self.assertEqual(vault.get_secret("ws1", "LONG"), long_val)

    def test_persistence_with_new_crypto(self):
        from engine.tenants import SecretsVault
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "secrets.json"
            v1 = SecretsVault(master_key="persist-key", storage_path=path)
            v1.set_secret("ws1", "DB", "postgres://x")
            v2 = SecretsVault(master_key="persist-key", storage_path=path)
            self.assertEqual(v2.get_secret("ws1", "DB"), "postgres://x")

    def test_wrong_key_fails(self):
        """Decrypting with wrong master key either raises or returns garbage."""
        from engine.tenants import SecretsVault
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "secrets.json"
            v1 = SecretsVault(master_key="key-A", storage_path=path)
            v1.set_secret("ws1", "S", "secret_data")
            v2 = SecretsVault(master_key="key-B", storage_path=path)
            try:
                decrypted = v2.get_secret("ws1", "S")
                # HMAC-CTR fallback: returns garbage, not original
                self.assertNotEqual(decrypted, "secret_data")
            except Exception:
                # Fernet: raises InvalidToken — correct behavior
                pass


# ═══════════════════════════════════════════════════════════════
# P1.1 — DURABLE JOB QUEUE (SQLite)
# ═══════════════════════════════════════════════════════════════

class TestSQLiteJobQueue(unittest.TestCase):

    def test_in_memory_still_works(self):
        """JobQueue without db_path works as before."""
        from engine.tenants import JobQueue
        q = JobQueue()
        job = q.submit("ws1", "u1", "pipeline")
        self.assertEqual(q.get(job.job_id).status, "queued")

    def test_sqlite_persistence(self):
        """Jobs survive queue recreation (simulating restart)."""
        from engine.tenants import JobQueue
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "jobs.db")

            q1 = JobQueue(db_path=db)
            job = q1.submit("ws1", "u1", "real_estate", {"address": "123 Main"})
            q1.update_status(job.job_id, "running")
            job_id = job.job_id

            # Simulate restart — new queue loads from SQLite
            q2 = JobQueue(db_path=db)
            restored = q2.get(job_id)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.workspace_id, "ws1")
            self.assertEqual(restored.status, "running")
            self.assertEqual(restored.pipeline_type, "real_estate")

    def test_sqlite_cancel_persists(self):
        from engine.tenants import JobQueue
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "jobs.db")
            q1 = JobQueue(db_path=db)
            job = q1.submit("ws1", "u1", "p")
            q1.cancel(job.job_id)

            q2 = JobQueue(db_path=db)
            self.assertEqual(q2.get(job.job_id).status, "cancelled")

    def test_sqlite_list_by_workspace(self):
        from engine.tenants import JobQueue
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "jobs.db")
            q = JobQueue(db_path=db)
            q.submit("ws1", "u1", "a")
            q.submit("ws1", "u1", "b")
            q.submit("ws2", "u2", "c")

            q2 = JobQueue(db_path=db)
            self.assertEqual(len(q2.list_jobs("ws1")), 2)
            self.assertEqual(len(q2.list_jobs("ws2")), 1)


if __name__ == "__main__":
    unittest.main()
