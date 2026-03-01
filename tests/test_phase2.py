"""
Phase 2 Test Suite — Tool Policy Gateway + Output Contracts
Run: cd /home/claude/phase1 && python -m unittest tests.test_phase2 -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — DENY-BY-DEFAULT
# ═══════════════════════════════════════════════════════════════

class TestPolicyBrokerDenyByDefault(unittest.TestCase):

    def test_unregistered_tool_blocked(self):
        """Tools without a registered policy are BLOCKED."""
        from engine.policy import PolicyBroker, PolicyViolation
        broker = PolicyBroker()
        with self.assertRaises(PolicyViolation) as ctx:
            broker.invoke("rogue_tool", lambda: "hacked", {})
        self.assertIn("NO_POLICY", str(ctx.exception))

    def test_registered_tool_allowed(self):
        """Tool with registered policy executes normally."""
        from engine.policy import PolicyBroker, ToolPolicy
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(tool_name="safe_tool"))
        result = broker.invoke("safe_tool", lambda: "ok", {})
        self.assertEqual(result, "ok")

    def test_tool_receives_kwargs(self):
        """Tool function receives kwargs from tool_input dict."""
        from engine.policy import PolicyBroker, ToolPolicy
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(tool_name="echo"))
        result = broker.invoke("echo", lambda msg="": f"echo:{msg}",
                               {"msg": "hello"})
        self.assertEqual(result, "echo:hello")


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — PATH CONTAINMENT
# ═══════════════════════════════════════════════════════════════

class TestPathContainment(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from engine.policy import PolicyBroker, ToolPolicy
        self.broker = PolicyBroker(output_dir=self.tmpdir)
        self.broker.register_policy(ToolPolicy(
            tool_name="writer",
            path_allowlist=[self.tmpdir],
        ))

    def test_valid_path_allowed(self):
        target = os.path.join(self.tmpdir, "report.txt")
        result = self.broker.invoke(
            "writer", lambda path="": f"wrote:{path}",
            {"path": target})
        self.assertIn("wrote:", result)

    def test_traversal_blocked(self):
        from engine.policy import PolicyViolation
        evil_path = os.path.join(self.tmpdir, "..", "..", "etc", "passwd")
        with self.assertRaises(PolicyViolation) as ctx:
            self.broker.invoke("writer", lambda path="": "ok",
                               {"path": evil_path})
        self.assertIn("PATH_ESCAPE", str(ctx.exception))

    def test_absolute_escape_blocked(self):
        from engine.policy import PolicyViolation
        with self.assertRaises(PolicyViolation) as ctx:
            self.broker.invoke("writer", lambda path="": "ok",
                               {"path": "/etc/crontab"})
        self.assertIn("PATH_ESCAPE", str(ctx.exception))

    def test_null_byte_blocked(self):
        from engine.policy import PolicyViolation
        with self.assertRaises(PolicyViolation) as ctx:
            self.broker.invoke("writer", lambda path="": "ok",
                               {"path": f"{self.tmpdir}/good\x00evil"})
        self.assertIn("PATH_ESCAPE", str(ctx.exception))


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — RATE LIMITING
# ═══════════════════════════════════════════════════════════════

class TestRateLimiting(unittest.TestCase):

    def test_stage_rate_limit(self):
        from engine.policy import PolicyBroker, ToolPolicy, PolicyViolation
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(
            tool_name="limited", max_calls_per_stage=3, max_calls_per_pipeline=100,
        ))
        for i in range(3):
            broker.invoke("limited", lambda: "ok", {}, stage_name="s1")
        with self.assertRaises(PolicyViolation) as ctx:
            broker.invoke("limited", lambda: "ok", {}, stage_name="s1")
        self.assertIn("RATE_LIMIT_STAGE", str(ctx.exception))

    def test_pipeline_rate_limit(self):
        from engine.policy import PolicyBroker, ToolPolicy, PolicyViolation
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(
            tool_name="limited", max_calls_per_stage=100, max_calls_per_pipeline=5,
        ))
        for i in range(5):
            broker.invoke("limited", lambda: "ok", {}, stage_name=f"s{i}")
        with self.assertRaises(PolicyViolation) as ctx:
            broker.invoke("limited", lambda: "ok", {}, stage_name="s99")
        self.assertIn("RATE_LIMIT_PIPELINE", str(ctx.exception))

    def test_different_stages_independent_counts(self):
        from engine.policy import PolicyBroker, ToolPolicy
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(
            tool_name="t", max_calls_per_stage=2, max_calls_per_pipeline=100,
        ))
        broker.invoke("t", lambda: "ok", {}, stage_name="s1")
        broker.invoke("t", lambda: "ok", {}, stage_name="s1")
        # s2 should have its own counter
        broker.invoke("t", lambda: "ok", {}, stage_name="s2")
        broker.invoke("t", lambda: "ok", {}, stage_name="s2")
        # Both at limit but didn't raise — success


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — OUTPUT SANITIZATION
# ═══════════════════════════════════════════════════════════════

class TestOutputSanitizer(unittest.TestCase):

    def test_ssn_redacted(self):
        from engine.policy import OutputSanitizer
        s = OutputSanitizer()
        self.assertIn("[REDACTED]", s.sanitize("SSN: 123-45-6789"))
        self.assertNotIn("123-45-6789", s.sanitize("SSN: 123-45-6789"))

    def test_credit_card_redacted(self):
        from engine.policy import OutputSanitizer
        s = OutputSanitizer()
        self.assertIn("[REDACTED]", s.sanitize("Card: 4111 1111 1111 1111"))

    def test_api_key_redacted(self):
        from engine.policy import OutputSanitizer
        s = OutputSanitizer()
        result = s.sanitize("api_key=sk-abc123xyz")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("sk-abc123xyz", result)

    def test_password_redacted(self):
        from engine.policy import OutputSanitizer
        s = OutputSanitizer()
        result = s.sanitize("password = SuperSecret123!")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("SuperSecret123!", result)

    def test_clean_text_unchanged(self):
        from engine.policy import OutputSanitizer
        s = OutputSanitizer()
        clean = "Market cap is $2.5 billion with 15,000 employees."
        self.assertEqual(s.sanitize(clean), clean)

    def test_broker_sanitizes_output(self):
        """PolicyBroker automatically sanitizes tool output."""
        from engine.policy import PolicyBroker, ToolPolicy
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(tool_name="lookup"))
        result = broker.invoke("lookup",
                               lambda: "SSN: 123-45-6789 found in records",
                               {})
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("123-45-6789", result)


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — AUDIT TRAIL
# ═══════════════════════════════════════════════════════════════

class TestPolicyAuditTrail(unittest.TestCase):

    def test_invocation_logged(self):
        from engine.policy import PolicyBroker, ToolPolicy
        from engine.observability import AuditLog
        audit = AuditLog()
        broker = PolicyBroker(audit=audit)
        broker.register_policy(ToolPolicy(tool_name="t"))
        broker.invoke("t", lambda: "ok", {}, stage_name="s1", user_id="u1")
        actions = [e.action for e in audit.entries]
        self.assertIn("tool.invoked", actions)
        self.assertIn("tool.completed", actions)

    def test_blocked_tool_logged(self):
        from engine.policy import PolicyBroker, PolicyViolation
        from engine.observability import AuditLog
        audit = AuditLog()
        broker = PolicyBroker(audit=audit)
        with self.assertRaises(PolicyViolation):
            broker.invoke("unregistered", lambda: "x", {})
        actions = [e.action for e in audit.entries]
        self.assertIn("tool.blocked", actions)

    def test_input_hashed_not_raw(self):
        """Audit log stores input hash, never raw values."""
        from engine.policy import PolicyBroker, ToolPolicy
        from engine.observability import AuditLog
        audit = AuditLog()
        broker = PolicyBroker(audit=audit)
        broker.register_policy(ToolPolicy(tool_name="t"))
        broker.invoke("t", lambda secret="": "ok",
                      {"secret": "my_password_123"}, stage_name="s1")
        invoked = [e for e in audit.entries if e.action == "tool.invoked"][0]
        self.assertIn("input_hash", invoked.details)
        self.assertNotIn("my_password_123", str(invoked.details))


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — HITL APPROVAL
# ═══════════════════════════════════════════════════════════════

class TestHITLApproval(unittest.TestCase):

    def test_human_approval_rejected(self):
        from engine.policy import PolicyBroker, ToolPolicy, ApprovalRequirement
        broker = PolicyBroker(hitl_callback=lambda *args: False)
        broker.register_policy(ToolPolicy(
            tool_name="dangerous",
            approval=ApprovalRequirement.HUMAN.value,
        ))
        result = broker.invoke("dangerous", lambda: "should not run", {})
        parsed = json.loads(result)
        self.assertTrue(parsed["blocked"])

    def test_human_approval_accepted(self):
        from engine.policy import PolicyBroker, ToolPolicy, ApprovalRequirement
        broker = PolicyBroker(hitl_callback=lambda *args: True)
        broker.register_policy(ToolPolicy(
            tool_name="dangerous",
            approval=ApprovalRequirement.HUMAN.value,
        ))
        result = broker.invoke("dangerous", lambda: "approved_result", {})
        self.assertEqual(result, "approved_result")


# ═══════════════════════════════════════════════════════════════
# TOOL POLICY — WRAP TOOL
# ═══════════════════════════════════════════════════════════════

class TestWrapTool(unittest.TestCase):

    def test_wrapped_tool_enforces_policy(self):
        from engine.policy import PolicyBroker, ToolPolicy, PolicyViolation
        broker = PolicyBroker()
        broker.register_policy(ToolPolicy(
            tool_name="t", max_calls_per_stage=2, max_calls_per_pipeline=100,
        ))
        wrapped = broker.wrap_tool(lambda: "ok", "t", stage_name="s1")
        self.assertEqual(wrapped(), "ok")
        self.assertEqual(wrapped(), "ok")
        with self.assertRaises(PolicyViolation):
            wrapped()  # 3rd call exceeds limit


# ═══════════════════════════════════════════════════════════════
# SANDBOXED FILE WRITER
# ═══════════════════════════════════════════════════════════════

class TestSandboxedFileWriter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from engine.policy import SandboxedFileWriter
        self.writer = SandboxedFileWriter(self.tmpdir)

    def test_write_within_sandbox(self):
        result = self.writer.write("report.txt", "hello world")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["path"], "report.txt")
        content = Path(self.tmpdir, "report.txt").read_text()
        self.assertEqual(content, "hello world")

    def test_write_nested_path(self):
        result = self.writer.write("reports/q3/model.json", '{"ok": true}')
        self.assertEqual(result["status"], "saved")
        self.assertTrue(Path(self.tmpdir, "reports/q3/model.json").exists())

    def test_traversal_blocked(self):
        result = self.writer.write("../../etc/passwd", "hacked")
        self.assertTrue(result.get("blocked"))
        self.assertIn("escapes", result.get("error", ""))
        self.assertFalse(Path("/etc/passwd_hacked").exists())

    def test_absolute_path_blocked(self):
        result = self.writer.write("/tmp/evil.txt", "x")
        self.assertTrue(result.get("blocked"))

    def test_size_limit(self):
        from engine.policy import SandboxedFileWriter
        small_writer = SandboxedFileWriter(self.tmpdir, max_file_size=100)
        result = small_writer.write("big.txt", "x" * 200)
        self.assertTrue(result.get("blocked"))
        self.assertIn("limit", result.get("error", ""))

    def test_read_within_sandbox(self):
        self.writer.write("data.txt", "content123")
        result = self.writer.read("data.txt")
        self.assertEqual(result["status"], "read")
        self.assertEqual(result["content"], "content123")

    def test_read_traversal_blocked(self):
        result = self.writer.read("../../etc/passwd")
        self.assertTrue(result.get("blocked"))

    def test_read_nonexistent(self):
        result = self.writer.read("ghost.txt")
        self.assertIn("not found", result.get("error", "").lower())


# ═══════════════════════════════════════════════════════════════
# POLICY FACTORIES
# ═══════════════════════════════════════════════════════════════

class TestPolicyFactories(unittest.TestCase):

    def test_file_writer_policy(self):
        from engine.policy import file_writer_policy, ActionScope
        p = file_writer_policy("/tmp/out")
        self.assertEqual(p.tool_name, "write_file")
        self.assertIn(ActionScope.WRITE.value, p.allowed_scopes)
        self.assertIn("/tmp/out", p.path_allowlist)

    def test_read_only_policy(self):
        from engine.policy import read_only_policy, ActionScope
        p = read_only_policy("search")
        self.assertEqual(p.tool_name, "search")
        self.assertIn(ActionScope.READ.value, p.allowed_scopes)
        self.assertNotIn(ActionScope.WRITE.value, p.allowed_scopes)

    def test_api_tool_policy(self):
        from engine.policy import api_tool_policy
        p = api_tool_policy("zillow_api", allowed_domains=["api.zillow.com"])
        self.assertTrue(p.allow_egress)
        self.assertIn("api.zillow.com", p.allowed_domains)


# ═══════════════════════════════════════════════════════════════
# OUTPUT CONTRACTS — REQUIRED FILES
# ═══════════════════════════════════════════════════════════════

class TestContractRequiredFiles(unittest.TestCase):

    def test_files_exist_passes(self):
        from engine.contracts import (
            StageContract, DeterministicValidator,
        )
        with tempfile.TemporaryDirectory() as d:
            Path(d, "report.md").write_text("# Report\nContent here")
            Path(d, "data.json").write_text('{"ok": true}')
            v = DeterministicValidator(d)
            contract = StageContract(
                name="test", stage_name="research",
                required_files=["report.md", "data.json"],
            )
            result = v.validate(contract)
            self.assertTrue(result.passed)
            self.assertEqual(len(result.errors), 0)
            # Evidence auto-created for each file
            self.assertGreaterEqual(len(result.evidence), 2)

    def test_missing_file_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        with tempfile.TemporaryDirectory() as d:
            v = DeterministicValidator(d)
            contract = StageContract(
                name="test", stage_name="research",
                required_files=["missing.md"],
            )
            result = v.validate(contract)
            self.assertFalse(result.passed)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("missing", result.errors[0]["message"])

    def test_empty_file_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        with tempfile.TemporaryDirectory() as d:
            Path(d, "empty.txt").write_text("")
            v = DeterministicValidator(d)
            contract = StageContract(
                name="test", stage_name="s",
                required_files=["empty.txt"],
            )
            result = v.validate(contract)
            self.assertFalse(result.passed)
            self.assertIn("empty", result.errors[0]["message"].lower())


# ═══════════════════════════════════════════════════════════════
# OUTPUT CONTRACTS — REQUIRED STATE FIELDS
# ═══════════════════════════════════════════════════════════════

class TestContractRequiredState(unittest.TestCase):

    def test_state_fields_present_passes(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            required_state_fields=["market_data", "score"],
        )
        state = {"market_data": {"cap": 100}, "score": 0.85}
        result = v.validate(contract, state=state)
        self.assertTrue(result.passed)

    def test_missing_state_field_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            required_state_fields=["market_data", "score"],
        )
        state = {"market_data": {"cap": 100}}
        result = v.validate(contract, state=state)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("score", result.errors[0]["message"])

    def test_empty_state_field_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            required_state_fields=["data"],
        )
        result = v.validate(contract, state={"data": {}})
        self.assertFalse(result.passed)

    def test_state_from_object(self):
        """Works with attribute-based state objects, not just dicts."""
        from engine.contracts import StageContract, DeterministicValidator

        class MyState:
            def __init__(self):
                self.score = 0.9
                self.notes = "good"

        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            required_state_fields=["score", "notes"],
        )
        result = v.validate(contract, state=MyState())
        self.assertTrue(result.passed)


# ═══════════════════════════════════════════════════════════════
# OUTPUT CONTRACTS — CUSTOM RULES
# ═══════════════════════════════════════════════════════════════

class TestContractCustomRules(unittest.TestCase):

    def test_min_length_passes(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "min_length", "field": "report", "value": 10}],
        )
        result = v.validate(contract, output={"report": "a" * 50})
        self.assertTrue(result.passed)

    def test_min_length_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "min_length", "field": "report", "value": 100}],
        )
        result = v.validate(contract, output={"report": "short"})
        self.assertFalse(result.passed)
        self.assertIn("min_length", result.errors[0]["rule"])

    def test_numeric_range_passes(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "numeric_range", "field": "score", "min": 0.0, "max": 1.0}],
        )
        result = v.validate(contract, output={"score": 0.85})
        self.assertTrue(result.passed)

    def test_numeric_range_too_low(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "numeric_range", "field": "score", "min": 0.5}],
        )
        result = v.validate(contract, output={"score": 0.1})
        self.assertFalse(result.passed)

    def test_numeric_range_too_high(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "numeric_range", "field": "pct", "max": 100}],
        )
        result = v.validate(contract, output={"pct": 150})
        self.assertFalse(result.passed)

    def test_regex_match_passes(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "regex_match", "field": "email", "pattern": r"@.*\.com"}],
        )
        result = v.validate(contract, output={"email": "user@test.com"})
        self.assertTrue(result.passed)

    def test_regex_match_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "regex_match", "field": "email", "pattern": r"@.*\.com"}],
        )
        result = v.validate(contract, output={"email": "nope"})
        self.assertFalse(result.passed)

    def test_not_null_passes(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "not_null", "field": "data"}],
        )
        result = v.validate(contract, output={"data": "present"})
        self.assertTrue(result.passed)

    def test_not_null_fails(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[{"rule": "not_null", "field": "data"}],
        )
        result = v.validate(contract, output={"other": "stuff"})
        self.assertFalse(result.passed)

    def test_file_min_size(self):
        from engine.contracts import StageContract, DeterministicValidator
        with tempfile.TemporaryDirectory() as d:
            Path(d, "big.txt").write_text("x" * 500)
            Path(d, "small.txt").write_text("x")
            v = DeterministicValidator(d)

            # Big file passes
            c1 = StageContract(name="t", stage_name="s",
                               rules=[{"rule": "file_min_size", "path": "big.txt", "min_bytes": 100}])
            self.assertTrue(v.validate(c1).passed)

            # Small file fails
            c2 = StageContract(name="t", stage_name="s",
                               rules=[{"rule": "file_min_size", "path": "small.txt", "min_bytes": 100}])
            self.assertFalse(v.validate(c2).passed)

    def test_multiple_rules(self):
        """Multiple rules all checked; first error blocks."""
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            rules=[
                {"rule": "not_null", "field": "a"},
                {"rule": "min_length", "field": "b", "value": 10},
                {"rule": "numeric_range", "field": "c", "min": 0, "max": 1},
            ],
        )
        result = v.validate(contract, output={"a": "ok", "b": "short", "c": 0.5})
        self.assertFalse(result.passed)
        # b fails min_length
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0]["rule"], "min_length")


# ═══════════════════════════════════════════════════════════════
# OUTPUT CONTRACTS — EVIDENCE & CONFIDENCE
# ═══════════════════════════════════════════════════════════════

class TestContractEvidence(unittest.TestCase):

    def test_evidence_requirement_met(self):
        from engine.contracts import (
            StageContract, DeterministicValidator, Evidence, EvidenceType,
        )
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            require_evidence=True, min_evidence_count=2,
        )
        evidence = [
            Evidence(evidence_type=EvidenceType.DATA.value, description="source A"),
            Evidence(evidence_type=EvidenceType.CITATION.value, description="ref B"),
        ]
        result = v.validate(contract, evidence=evidence)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.evidence), 2)

    def test_evidence_requirement_not_met(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            require_evidence=True, min_evidence_count=3,
        )
        result = v.validate(contract, evidence=[])
        self.assertFalse(result.passed)
        self.assertIn("evidence", result.errors[0]["message"].lower())

    def test_confidence_threshold_warning(self):
        from engine.contracts import (
            StageContract, DeterministicValidator, ConfidenceScore,
        )
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            min_confidence=0.8,
        )
        conf = ConfidenceScore(overall=0.5)
        result = v.validate(contract, confidence=conf)
        # Confidence below threshold is WARNING not ERROR
        self.assertTrue(result.passed)
        self.assertEqual(len(result.warnings), 1)


# ═══════════════════════════════════════════════════════════════
# CONTRACT REGISTRY
# ═══════════════════════════════════════════════════════════════

class TestContractRegistry(unittest.TestCase):

    def test_register_and_validate(self):
        from engine.contracts import ContractRegistry, StageContract
        reg = ContractRegistry()
        reg.register(StageContract(
            name="research_contract", stage_name="research",
            required_state_fields=["market_data"],
        ))
        result = reg.validate_stage("research",
                                     state={"market_data": {"cap": 100}})
        self.assertTrue(result.passed)

    def test_no_contract_passes_with_info(self):
        from engine.contracts import ContractRegistry
        reg = ContractRegistry()
        result = reg.validate_stage("unregistered")
        self.assertTrue(result.passed)
        self.assertEqual(len(result.infos), 1)
        self.assertIn("no_contract", result.infos[0]["rule"])

    def test_registry_with_output_dir(self):
        from engine.contracts import ContractRegistry, StageContract
        with tempfile.TemporaryDirectory() as d:
            Path(d, "model.json").write_text('{"revenue": 1000000}')
            reg = ContractRegistry()
            reg.set_output_dir(d)
            reg.register(StageContract(
                name="fin_contract", stage_name="financials",
                required_files=["model.json"],
            ))
            result = reg.validate_stage("financials")
            self.assertTrue(result.passed)

    def test_validation_result_summary(self):
        from engine.contracts import StageContract, DeterministicValidator
        v = DeterministicValidator()
        contract = StageContract(
            name="test", stage_name="s",
            required_state_fields=["a", "b"],
        )
        result = v.validate(contract, state={"a": "ok"})
        s = result.summary()
        self.assertEqual(s["stage"], "s")
        self.assertFalse(s["passed"])
        self.assertEqual(s["errors"], 1)


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: POLICY + CONTRACTS TOGETHER
# ═══════════════════════════════════════════════════════════════

class TestPolicyContractsIntegration(unittest.TestCase):
    """Verify that policy-gated file writes + contract validation work end-to-end."""

    def test_write_then_validate(self):
        from engine.policy import SandboxedFileWriter
        from engine.contracts import ContractRegistry, StageContract

        with tempfile.TemporaryDirectory() as d:
            writer = SandboxedFileWriter(d)
            writer.write("reports/analysis.md", "# Analysis\n" + "data " * 100)
            writer.write("data/model.json", '{"revenue": 5000000}')

            reg = ContractRegistry()
            reg.set_output_dir(d)
            reg.register(StageContract(
                name="analysis_contract", stage_name="analysis",
                required_files=["reports/analysis.md", "data/model.json"],
                rules=[
                    {"rule": "file_min_size", "path": "reports/analysis.md", "min_bytes": 50},
                ],
            ))

            result = reg.validate_stage("analysis")
            self.assertTrue(result.passed)
            self.assertGreaterEqual(len(result.evidence), 2)

    def test_blocked_write_fails_contract(self):
        """If policy blocks a write, the contract fails (file missing)."""
        from engine.policy import SandboxedFileWriter
        from engine.contracts import ContractRegistry, StageContract

        with tempfile.TemporaryDirectory() as d:
            writer = SandboxedFileWriter(d)
            # This traversal gets blocked
            writer.write("../../etc/evil.txt", "bad")

            reg = ContractRegistry()
            reg.set_output_dir(d)
            reg.register(StageContract(
                name="c", stage_name="s",
                required_files=["../../etc/evil.txt"],
            ))
            result = reg.validate_stage("s")
            self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
