"""
P1 Test Suite — Enterprise Hardening
Run: cd /home/claude/phase1 && python -m unittest tests.test_p1_fixes -v

Covers:
  P1.2 — JWT auth + API key auth + middleware
  P1.3 — OTel semantic conventions
  P1.4 — Cost circuit breaker + per-stage budgets
  P1.5 — DLP scanner, egress policy, connector permissions
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# P1.2 — JWT AUTH
# ═══════════════════════════════════════════════════════════════

class TestJWTAuth(unittest.TestCase):

    def _make_token(self, payload, secret="test-secret"):
        from engine.auth import build_hs256_token
        return build_hs256_token(payload, secret)

    def test_valid_hs256_token(self):
        from engine.auth import JWTValidator
        secret = "my-test-secret"
        token = self._make_token({
            "sub": "user123", "exp": int(time.time()) + 3600,
            "workspace_id": "ws1", "email": "u@co.com",
            "roles": ["admin"],
        }, secret)
        v = JWTValidator(secret=secret)
        result = v.validate(token)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.user_id, "user123")
        self.assertEqual(result.workspace_id, "ws1")
        self.assertEqual(result.email, "u@co.com")
        self.assertEqual(result.method, "jwt")

    def test_expired_token_rejected(self):
        from engine.auth import JWTValidator
        secret = "s"
        token = self._make_token({
            "sub": "u", "exp": int(time.time()) - 3600,
        }, secret)
        v = JWTValidator(secret=secret)
        result = v.validate(token)
        self.assertFalse(result.authenticated)
        self.assertIn("expired", result.error.lower())

    def test_wrong_secret_rejected(self):
        from engine.auth import JWTValidator
        token = self._make_token({"sub": "u", "exp": int(time.time()) + 3600}, "key-A")
        v = JWTValidator(secret="key-B")
        result = v.validate(token)
        self.assertFalse(result.authenticated)
        self.assertIn("signature", result.error.lower())

    def test_missing_required_claim(self):
        from engine.auth import JWTValidator
        secret = "s"
        token = self._make_token({"exp": int(time.time()) + 3600}, secret)
        v = JWTValidator(secret=secret, required_claims=["sub", "exp"])
        result = v.validate(token)
        self.assertFalse(result.authenticated)
        self.assertIn("sub", result.error)

    def test_bearer_prefix_stripped(self):
        from engine.auth import JWTValidator
        secret = "s"
        token = self._make_token({"sub": "u", "exp": int(time.time()) + 3600}, secret)
        v = JWTValidator(secret=secret)
        result = v.validate(f"Bearer {token}")
        self.assertTrue(result.authenticated)

    def test_malformed_token(self):
        from engine.auth import JWTValidator
        v = JWTValidator(secret="s")
        result = v.validate("not.a.valid.jwt.at.all")
        self.assertFalse(result.authenticated)

    def test_empty_token(self):
        from engine.auth import JWTValidator
        v = JWTValidator(secret="s")
        result = v.validate("")
        self.assertFalse(result.authenticated)

    def test_audience_check(self):
        from engine.auth import JWTValidator
        secret = "s"
        token = self._make_token({
            "sub": "u", "exp": int(time.time()) + 3600, "aud": "other-api",
        }, secret)
        v = JWTValidator(secret=secret, audience="my-api")
        result = v.validate(token)
        self.assertFalse(result.authenticated)
        self.assertIn("audience", result.error.lower())

    def test_issuer_check(self):
        from engine.auth import JWTValidator
        secret = "s"
        token = self._make_token({
            "sub": "u", "exp": int(time.time()) + 3600, "iss": "evil.com",
        }, secret)
        v = JWTValidator(secret=secret, issuer="auth.good.com")
        result = v.validate(token)
        self.assertFalse(result.authenticated)
        self.assertIn("issuer", result.error.lower())


# ═══════════════════════════════════════════════════════════════
# P1.2 — API KEY AUTH
# ═══════════════════════════════════════════════════════════════

class TestAPIKeyAuth(unittest.TestCase):

    def test_create_and_validate(self):
        from engine.auth import APIKeyAuth
        auth = APIKeyAuth()
        key_id, raw_key = auth.create_key("ws1", "u1", scopes=["pipeline.run"])
        result = auth.validate(raw_key)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.user_id, "u1")
        self.assertEqual(result.workspace_id, "ws1")
        self.assertEqual(result.scopes, ["pipeline.run"])
        self.assertEqual(result.method, "api_key")

    def test_invalid_key_rejected(self):
        from engine.auth import APIKeyAuth
        auth = APIKeyAuth()
        result = auth.validate("ae_boguskey12345")
        self.assertFalse(result.authenticated)

    def test_revoked_key_rejected(self):
        from engine.auth import APIKeyAuth
        auth = APIKeyAuth()
        key_id, raw_key = auth.create_key("ws1", "u1")
        auth.revoke(key_id)
        result = auth.validate(raw_key)
        self.assertFalse(result.authenticated)
        self.assertIn("revoked", result.error.lower())

    def test_key_hash_not_plaintext(self):
        """Raw key is NOT stored — only hash is stored."""
        from engine.auth import APIKeyAuth
        auth = APIKeyAuth()
        _, raw_key = auth.create_key("ws1", "u1")
        for record in auth._keys.values():
            self.assertNotEqual(record.key_hash, raw_key)
            self.assertNotIn("ae_", record.key_hash)


# ═══════════════════════════════════════════════════════════════
# P1.2 — AUTH MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class TestAuthMiddleware(unittest.TestCase):

    def test_jwt_auth(self):
        from engine.auth import AuthMiddleware, JWTValidator, build_hs256_token
        secret = "s"
        token = build_hs256_token({
            "sub": "u1", "exp": int(time.time()) + 3600, "workspace_id": "ws1",
        }, secret)
        mw = AuthMiddleware(jwt_validator=JWTValidator(secret=secret))
        result = mw.authenticate({"Authorization": f"Bearer {token}"})
        self.assertTrue(result.authenticated)
        self.assertEqual(result.method, "jwt")

    def test_api_key_auth(self):
        from engine.auth import AuthMiddleware, APIKeyAuth
        api_auth = APIKeyAuth()
        _, raw_key = api_auth.create_key("ws1", "u1")
        mw = AuthMiddleware(api_key_auth=api_auth)
        result = mw.authenticate({"X-Api-Key": raw_key})
        self.assertTrue(result.authenticated)
        self.assertEqual(result.method, "api_key")

    def test_header_auth_disabled_by_default(self):
        from engine.auth import AuthMiddleware
        mw = AuthMiddleware()
        result = mw.authenticate({"X-User-Id": "u1", "X-Workspace-Id": "ws1"})
        self.assertFalse(result.authenticated)

    def test_header_auth_enabled_for_dev(self):
        from engine.auth import AuthMiddleware
        mw = AuthMiddleware(allow_header_auth=True)
        result = mw.authenticate({"X-User-Id": "u1", "X-Workspace-Id": "ws1"})
        self.assertTrue(result.authenticated)
        self.assertEqual(result.method, "header")

    def test_no_credentials_rejected(self):
        from engine.auth import AuthMiddleware
        mw = AuthMiddleware()
        result = mw.authenticate({})
        self.assertFalse(result.authenticated)

    def test_auth_audit_logged(self):
        from engine.auth import AuthMiddleware, JWTValidator, build_hs256_token
        from engine.observability import AuditLog
        audit = AuditLog()
        secret = "s"
        token = build_hs256_token({"sub": "u1", "exp": int(time.time()) + 3600}, secret)
        mw = AuthMiddleware(jwt_validator=JWTValidator(secret=secret), audit=audit)
        mw.authenticate({"Authorization": f"Bearer {token}"})
        self.assertTrue(any(e.action == "auth.success" for e in audit.entries))


# ═══════════════════════════════════════════════════════════════
# P1.3 — OTEL SEMANTIC CONVENTIONS
# ═══════════════════════════════════════════════════════════════

class TestOTelConventions(unittest.TestCase):

    def test_span_has_resource_attributes(self):
        from engine.observability import Tracer
        t = Tracer(service_name="test-svc", service_version="1.0.0",
                   environment="staging")
        with t.span("op") as s:
            pass
        span = t.spans[0]
        self.assertEqual(span.resource["service.name"], "test-svc")
        self.assertEqual(span.resource["service.version"], "1.0.0")
        self.assertEqual(span.resource["deployment.environment"], "staging")

    def test_span_status_codes(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("ok_op"):
            pass
        self.assertEqual(t.spans[0].status, "STATUS_OK")

        try:
            with t.span("err_op"):
                raise ValueError("test")
        except ValueError:
            pass
        self.assertEqual(t.spans[1].status, "STATUS_ERROR")
        self.assertEqual(t.spans[1].status_message, "test")

    def test_span_kind(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("client_call", kind="CLIENT"):
            pass
        self.assertEqual(t.spans[0].kind, "CLIENT")

    def test_traceparent_format(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("op") as s:
            tp = s.traceparent
        # W3C traceparent: version-traceid-spanid-flags
        parts = tp.split("-")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], "00")
        self.assertEqual(parts[3], "01")

    def test_span_events(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("op") as s:
            s.add_event("cache.miss", {"key": "user_123"})
            s.add_event("db.query", {"table": "users"})
        self.assertEqual(len(t.spans[0].events), 2)
        self.assertEqual(t.spans[0].events[0]["name"], "cache.miss")

    def test_otel_export_format(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("op"):
            pass
        exported = t.spans[0].to_otel_dict()
        self.assertIn("traceId", exported)
        self.assertIn("spanId", exported)
        self.assertEqual(exported["kind"], "SPAN_KIND_INTERNAL")
        self.assertIn("startTimeUnixNano", exported)
        self.assertIn("resource", exported)

    def test_tracer_traceparent_propagation(self):
        from engine.observability import Tracer
        t = Tracer()
        tp = t.traceparent
        self.assertTrue(tp.startswith("00-"))
        self.assertIn(t.trace_id, tp)


# ═══════════════════════════════════════════════════════════════
# P1.4 — COST CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════

class TestCostCircuitBreaker(unittest.TestCase):

    def test_enforce_budget_passes_within_limit(self):
        from engine.observability import CostMeter
        m = CostMeter(budget_limit_usd=100.0)
        m.record("a", "openai/gpt-4.1", 1000, 500)
        # Should not raise
        m.enforce_budget()

    def test_enforce_budget_blocks_over_limit(self):
        from engine.observability import CostMeter, BudgetExceededError
        m = CostMeter(budget_limit_usd=0.001)  # Tiny budget
        m.record("a", "openai/gpt-4.1", 100000, 50000)  # Exceeds
        with self.assertRaises(BudgetExceededError):
            m.enforce_budget()

    def test_circuit_breaker_trips(self):
        from engine.observability import CostMeter
        m = CostMeter(budget_limit_usd=0.001)
        m.record("a", "openai/gpt-4.1", 100000, 50000)
        self.assertTrue(m.circuit_open)

    def test_circuit_breaker_blocks_all_subsequent(self):
        from engine.observability import CostMeter, BudgetExceededError
        m = CostMeter(budget_limit_usd=0.001)
        m.record("a", "openai/gpt-4.1", 100000, 50000)
        # Even a tiny call is blocked
        with self.assertRaises(BudgetExceededError) as ctx:
            m.enforce_budget()
        self.assertIn("circuit_breaker", str(ctx.exception))

    def test_circuit_breaker_manual_reset(self):
        from engine.observability import CostMeter
        m = CostMeter(budget_limit_usd=0.001)
        m.record("a", "openai/gpt-4.1", 100000, 50000)
        self.assertTrue(m.circuit_open)
        m.reset_circuit()
        self.assertFalse(m.circuit_open)

    def test_per_stage_budget(self):
        from engine.observability import CostMeter, BudgetExceededError
        m = CostMeter(
            budget_limit_usd=100.0,
            stage_budgets={"research": 0.001},
        )
        m.record("a", "openai/gpt-4.1", 100000, 50000, stage_name="research")
        with self.assertRaises(BudgetExceededError) as ctx:
            m.enforce_budget(stage_name="research")
        self.assertIn("stage:research", str(ctx.exception))

    def test_token_runaway_detection(self):
        from engine.observability import CostMeter, BudgetExceededError
        m = CostMeter(budget_limit_usd=100.0, max_tokens_per_call=10000)
        with self.assertRaises(BudgetExceededError) as ctx:
            m.enforce_budget(estimated_tokens=50000)
        self.assertIn("token_runaway", str(ctx.exception))

    def test_stage_cost_tracking(self):
        from engine.observability import CostMeter
        m = CostMeter()
        m.record("a", "openai/gpt-4.1", 10000, 5000, stage_name="research")
        m.record("a", "openai/gpt-4.1", 10000, 5000, stage_name="model")
        m.record("a", "openai/gpt-4.1", 10000, 5000, stage_name="research")
        research_cost = m.stage_cost_usd("research")
        model_cost = m.stage_cost_usd("model")
        self.assertGreater(research_cost, model_cost)


# ═══════════════════════════════════════════════════════════════
# P1.5 — DLP SCANNER
# ═══════════════════════════════════════════════════════════════

class TestDLPScanner(unittest.TestCase):

    def test_detects_ssn(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        result = dlp.scan("User SSN is 123-45-6789")
        self.assertFalse(result.clean)
        self.assertTrue(result.blocked)
        self.assertTrue(any(f["pattern"] == "ssn" for f in result.findings))

    def test_detects_credit_card(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        result = dlp.scan("Card: 4111-1111-1111-1111")
        self.assertFalse(result.clean)

    def test_detects_api_key(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        result = dlp.scan("key: sk-abc123xyz456789012")
        self.assertFalse(result.clean)

    def test_detects_aws_key(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        result = dlp.scan("Access key: AKIAIOSFODNN7EXAMPLE")
        self.assertFalse(result.clean)

    def test_clean_data_passes(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        result = dlp.scan("Revenue projection: $5M for Q3 2025")
        self.assertTrue(result.clean)
        self.assertFalse(result.blocked)

    def test_redact(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        redacted = dlp.redact("SSN: 123-45-6789 Key: sk-abc123xyz456789012")
        self.assertNotIn("123-45-6789", redacted)
        self.assertIn("REDACTED", redacted)

    def test_scan_dict_input(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner()
        result = dlp.scan({"user": {"ssn": "123-45-6789"}})
        self.assertFalse(result.clean)

    def test_non_blocking_mode(self):
        from engine.connectors import DLPScanner
        dlp = DLPScanner(block_on_match=False)
        result = dlp.scan("SSN: 123-45-6789")
        self.assertFalse(result.clean)
        self.assertFalse(result.blocked)  # Detect but don't block


# ═══════════════════════════════════════════════════════════════
# P1.5 — EGRESS POLICY
# ═══════════════════════════════════════════════════════════════

class TestEgressPolicy(unittest.TestCase):

    def test_allowed_domain_passes(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(allowed_domains=["api.zillow.com"])
        self.assertTrue(ep.check_url("https://api.zillow.com/v2/property"))

    def test_blocked_domain_fails(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(blocked_domains=["evil.com"])
        self.assertFalse(ep.check_url("https://evil.com/exfil"))

    def test_subdomain_blocked(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(blocked_domains=["evil.com"])
        self.assertFalse(ep.check_url("https://api.evil.com/data"))

    def test_unlisted_domain_blocked_with_allowlist(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(allowed_domains=["api.zillow.com"])
        self.assertFalse(ep.check_url("https://api.random.com/data"))

    def test_allow_all_dev_mode(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(allow_all=True)
        self.assertTrue(ep.check_url("https://anything.com"))

    def test_blocklist_overrides_allowlist(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(
            allowed_domains=["api.example.com"],
            blocked_domains=["api.example.com"],
        )
        self.assertFalse(ep.check_url("https://api.example.com/data"))

    def test_check_domain(self):
        from engine.connectors import EgressPolicy
        ep = EgressPolicy(allowed_domains=["api.zillow.com"])
        self.assertTrue(ep.check_domain("api.zillow.com"))
        self.assertFalse(ep.check_domain("other.com"))


# ═══════════════════════════════════════════════════════════════
# P1.5 — CONNECTOR PERMISSIONS
# ═══════════════════════════════════════════════════════════════

class TestConnectorPermission(unittest.TestCase):

    def test_allowed_tool(self):
        from engine.connectors import ConnectorPermission
        perm = ConnectorPermission(
            connector_id="c1", workspace_id="ws1",
            allowed_tools=["search", "read"],
        )
        self.assertTrue(perm.is_tool_allowed("search"))
        self.assertFalse(perm.is_tool_allowed("delete"))

    def test_blocked_tool(self):
        from engine.connectors import ConnectorPermission
        perm = ConnectorPermission(
            connector_id="c1", workspace_id="ws1",
            blocked_tools=["delete", "write"],
        )
        self.assertTrue(perm.is_tool_allowed("search"))
        self.assertFalse(perm.is_tool_allowed("delete"))

    def test_rate_limit(self):
        from engine.connectors import ConnectorPermission
        perm = ConnectorPermission(
            connector_id="c1", workspace_id="ws1",
            max_calls_per_hour=3,
        )
        self.assertTrue(perm.check_rate_limit())
        self.assertTrue(perm.check_rate_limit())
        self.assertTrue(perm.check_rate_limit())
        self.assertFalse(perm.check_rate_limit())  # 4th blocked

    def test_empty_allowlist_means_all(self):
        from engine.connectors import ConnectorPermission
        perm = ConnectorPermission(
            connector_id="c1", workspace_id="ws1",
        )
        self.assertTrue(perm.is_tool_allowed("anything"))


if __name__ == "__main__":
    unittest.main()
