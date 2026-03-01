"""
Phase 4 Test Suite — Connectors, Core, API, Integration
Run: cd /home/claude/phase1 && python -m unittest tests.test_phase4 -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# TOOL SCHEMA
# ═══════════════════════════════════════════════════════════════

class TestToolSchema(unittest.TestCase):

    def test_to_json_schema(self):
        from engine.connectors import ToolSchema, ToolParameter
        schema = ToolSchema(
            name="get_property",
            description="Get property details",
            parameters=[
                ToolParameter(name="zpid", param_type="string",
                              description="Zillow ID", required=True),
                ToolParameter(name="include_history", param_type="boolean",
                              description="Include price history", required=False,
                              default=False),
            ],
        )
        js = schema.to_json_schema()
        self.assertEqual(js["type"], "function")
        self.assertEqual(js["function"]["name"], "get_property")
        props = js["function"]["parameters"]["properties"]
        self.assertIn("zpid", props)
        self.assertIn("include_history", props)
        self.assertEqual(js["function"]["parameters"]["required"], ["zpid"])

    def test_to_dict(self):
        from engine.connectors import ToolSchema, ToolParameter
        schema = ToolSchema(
            name="search", description="Search",
            parameters=[ToolParameter(name="q", description="query")],
            connector_id="google",
        )
        d = schema.to_dict()
        self.assertEqual(d["name"], "search")
        self.assertEqual(d["connector"], "google")


# ═══════════════════════════════════════════════════════════════
# MCP CONNECTOR
# ═══════════════════════════════════════════════════════════════

class TestMCPConnector(unittest.TestCase):

    def test_register_and_invoke_tool(self):
        from engine.connectors import MCPConnector, ToolSchema
        mcp = MCPConnector("test_mcp", "Test MCP")
        mcp.register_tool(
            ToolSchema(name="echo", description="Echo input"),
            handler=lambda msg="": f"echo:{msg}",
        )
        mcp.connect({})
        self.assertTrue(mcp.is_connected)

        result = mcp.invoke_tool("echo", {"msg": "hello"})
        self.assertEqual(result, "echo:hello")

    def test_list_tools(self):
        from engine.connectors import MCPConnector, ToolSchema
        mcp = MCPConnector("mcp1", "MCP One")
        mcp.register_tool(ToolSchema(name="t1", description="Tool 1"))
        mcp.register_tool(ToolSchema(name="t2", description="Tool 2"))
        mcp.connect({})
        tools = mcp.list_tools()
        self.assertEqual(len(tools), 2)
        self.assertEqual({t.name for t in tools}, {"t1", "t2"})

    def test_health_check(self):
        from engine.connectors import MCPConnector
        mcp = MCPConnector("mcp", "MCP")
        h = mcp.health_check()
        self.assertEqual(h.status, "unhealthy")
        mcp.connect({})
        h = mcp.health_check()
        self.assertEqual(h.status, "healthy")

    def test_disconnect(self):
        from engine.connectors import MCPConnector
        mcp = MCPConnector("mcp", "MCP")
        mcp.connect({})
        self.assertTrue(mcp.is_connected)
        mcp.disconnect()
        self.assertFalse(mcp.is_connected)

    def test_unknown_tool(self):
        from engine.connectors import MCPConnector
        mcp = MCPConnector("mcp", "MCP")
        mcp.connect({})
        result = mcp.invoke_tool("nonexistent", {})
        self.assertIn("error", result)


# ═══════════════════════════════════════════════════════════════
# HTTP API CONNECTOR
# ═══════════════════════════════════════════════════════════════

class TestHTTPAPIConnector(unittest.TestCase):

    def test_connect_sets_auth(self):
        from engine.connectors import HTTPAPIConnector
        conn = HTTPAPIConnector("zillow", "Zillow",
                                config={"base_url": "https://api.zillow.com"})
        conn.connect({"api_key": "test123"})
        self.assertTrue(conn.is_connected)
        self.assertIn("Authorization", conn._default_headers)

    def test_list_tools_from_endpoints(self):
        from engine.connectors import HTTPAPIConnector, EndpointConfig, ToolParameter
        conn = HTTPAPIConnector("api", "API", config={"base_url": "http://localhost"})
        conn.add_endpoint(EndpointConfig(
            tool_name="get_data", method="GET", path="/data",
            description="Get data",
            parameters=[ToolParameter(name="id", description="Record ID")],
        ))
        conn.add_endpoint(EndpointConfig(
            tool_name="post_data", method="POST", path="/data",
            description="Post data",
        ))
        conn.connect({})
        tools = conn.list_tools()
        self.assertEqual(len(tools), 2)
        self.assertEqual({t.name for t in tools}, {"get_data", "post_data"})

    def test_unknown_tool_returns_error(self):
        from engine.connectors import HTTPAPIConnector
        conn = HTTPAPIConnector("api", "API", config={"base_url": "http://localhost"})
        conn.connect({})
        result = conn.invoke_tool("ghost", {})
        self.assertIn("error", result)


# ═══════════════════════════════════════════════════════════════
# CONNECTOR REGISTRY
# ═══════════════════════════════════════════════════════════════

class TestConnectorRegistry(unittest.TestCase):

    def test_register_and_list(self):
        from engine.connectors import ConnectorRegistry, MCPConnector
        reg = ConnectorRegistry()
        mcp = MCPConnector("mcp1", "MCP 1")
        reg.register(mcp, workspace_ids=["ws1"])
        self.assertEqual(len(reg.list_connectors("ws1")), 1)
        self.assertEqual(len(reg.list_connectors("ws2")), 0)

    def test_all_tools_aggregated(self):
        from engine.connectors import ConnectorRegistry, MCPConnector, ToolSchema
        reg = ConnectorRegistry()
        mcp1 = MCPConnector("m1", "M1")
        mcp1.register_tool(ToolSchema(name="t1", description="T1"))
        mcp1.connect({})
        mcp2 = MCPConnector("m2", "M2")
        mcp2.register_tool(ToolSchema(name="t2", description="T2"))
        mcp2.connect({})
        reg.register(mcp1)
        reg.register(mcp2)
        tools = reg.all_tools()
        self.assertEqual(len(tools), 2)

    def test_invoke_with_audit(self):
        from engine.connectors import ConnectorRegistry, MCPConnector, ToolSchema
        from engine.observability import AuditLog
        audit = AuditLog()
        reg = ConnectorRegistry(audit=audit)
        mcp = MCPConnector("m1", "M1")
        mcp.register_tool(
            ToolSchema(name="echo", description="Echo"),
            handler=lambda msg="": f"echo:{msg}",
        )
        mcp.connect({})
        reg.register(mcp)
        result = reg.invoke("m1", "echo", {"msg": "hi"}, user_id="u1")
        self.assertEqual(result, "echo:hi")
        actions = [e.action for e in audit.entries]
        self.assertIn("connector.invoke", actions)

    def test_workspace_access_denied(self):
        from engine.connectors import ConnectorRegistry, MCPConnector
        reg = ConnectorRegistry()
        mcp = MCPConnector("m1", "M1")
        reg.register(mcp, workspace_ids=["ws1"])
        result = reg.invoke("m1", "t", {}, workspace_id="ws2")
        self.assertIn("error", result)
        self.assertIn("not accessible", result["error"])

    def test_unregister(self):
        from engine.connectors import ConnectorRegistry, MCPConnector
        reg = ConnectorRegistry()
        mcp = MCPConnector("m1", "M1")
        mcp.connect({})
        reg.register(mcp)
        self.assertIsNotNone(reg.get("m1"))
        reg.unregister("m1")
        self.assertIsNone(reg.get("m1"))

    def test_health_all(self):
        from engine.connectors import ConnectorRegistry, MCPConnector
        reg = ConnectorRegistry()
        m1 = MCPConnector("m1", "M1")
        m1.connect({})
        m2 = MCPConnector("m2", "M2")
        reg.register(m1)
        reg.register(m2)
        health = reg.health_all()
        self.assertEqual(len(health), 2)
        self.assertEqual(health["m1"].status, "healthy")
        self.assertEqual(health["m2"].status, "unhealthy")


# ═══════════════════════════════════════════════════════════════
# CREDENTIAL SCOPE
# ═══════════════════════════════════════════════════════════════

class TestCredentialScope(unittest.TestCase):

    def test_resolve_from_vault(self):
        from engine.connectors import CredentialScope
        from engine.tenants import SecretsVault
        vault = SecretsVault(master_key="k")
        vault.set_secret("ws1", "API_KEY", "secret123")
        vault.set_secret("ws1", "DB_URL", "pg://x")

        scope = CredentialScope(
            connector_id="zillow", workspace_id="ws1",
            required_keys=["API_KEY", "DB_URL", "MISSING"],
        )
        creds = scope.resolve(vault.get_secret)
        self.assertEqual(creds["API_KEY"], "secret123")
        self.assertEqual(creds["DB_URL"], "pg://x")
        self.assertNotIn("MISSING", creds)


# ═══════════════════════════════════════════════════════════════
# OODA LOOP
# ═══════════════════════════════════════════════════════════════

class TestOODALoop(unittest.TestCase):

    def test_converges_first_iteration(self):
        from engine.core import OODALoop
        from engine.contracts import ContractRegistry, StageContract
        reg = ContractRegistry()
        reg.register(StageContract(
            name="c", stage_name="s",
            required_state_fields=["data"],
        ))
        ooda = OODALoop(max_iterations=3, contract_registry=reg)
        result = ooda.run("s", act_fn=lambda: {"data": "ok"},
                           state={"data": "present"})
        self.assertTrue(result.converged)
        self.assertEqual(result.iteration, 1)
        self.assertEqual(result.decision, "accept")

    def test_converges_after_retries(self):
        from engine.core import OODALoop
        from engine.contracts import ContractRegistry, StageContract
        with tempfile.TemporaryDirectory() as d:
            reg = ContractRegistry()
            reg.set_output_dir(d)
            reg.register(StageContract(
                name="c", stage_name="s",
                required_files=["result.txt"],
            ))

            call_count = [0]
            def act():
                call_count[0] += 1
                if call_count[0] >= 2:
                    Path(d, "result.txt").write_text("done")
                return "output"

            ooda = OODALoop(max_iterations=3, contract_registry=reg)
            result = ooda.run("s", act_fn=act, output_dir=d)
            self.assertTrue(result.converged)
            self.assertEqual(result.iteration, 2)

    def test_exhausts_iterations(self):
        from engine.core import OODALoop
        from engine.contracts import ContractRegistry, StageContract
        reg = ContractRegistry()
        reg.register(StageContract(
            name="c", stage_name="s",
            required_state_fields=["never_set"],
        ))
        ooda = OODALoop(max_iterations=2, contract_registry=reg)
        result = ooda.run("s", act_fn=lambda: "irrelevant",
                           state={"other": "stuff"})
        self.assertFalse(result.converged)
        self.assertEqual(result.decision, "exhausted")
        self.assertEqual(result.iteration, 2)

    def test_handles_act_fn_exception(self):
        from engine.core import OODALoop
        ooda = OODALoop(max_iterations=2)
        result = ooda.run("s", act_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertFalse(result.converged)
        self.assertEqual(result.decision, "exhausted")

    def test_no_contract_passes(self):
        from engine.core import OODALoop
        ooda = OODALoop(max_iterations=1)
        result = ooda.run("unregistered", act_fn=lambda: "ok")
        self.assertTrue(result.converged)


# ═══════════════════════════════════════════════════════════════
# AGENTIC PIPELINE
# ═══════════════════════════════════════════════════════════════

class TestAgenticPipeline(unittest.TestCase):

    def test_pipeline_construction(self):
        from engine.core import AgenticPipeline, PipelineConfig, AgentDefinition
        from engine.runtime import StageDef
        from engine.contracts import StageContract
        from engine.policy import ToolPolicy

        with tempfile.TemporaryDirectory() as d:
            config = PipelineConfig(
                name="test_pipeline",
                version="1.0.0",
                stages=[
                    StageDef(name="research", handler="h_research"),
                    StageDef(name="model", handler="h_model", depends_on=["research"]),
                ],
                agents=[
                    AgentDefinition(name="analyst", role="Analyst",
                                    goal="Analyze market", llm_tier="light",
                                    temperature=0.3),
                ],
                contracts=[
                    StageContract(name="c1", stage_name="research",
                                  required_state_fields=["data"]),
                ],
                tool_policies=[
                    ToolPolicy(tool_name="search"),
                ],
                output_dir=d,
            )
            pipe = AgenticPipeline(config, tenant_id="t1", user_id="u1")

            self.assertIsNotNone(pipe.tracer)
            self.assertIsNotNone(pipe.audit)
            self.assertIsNotNone(pipe.cost_meter)
            self.assertIsNotNone(pipe.policy_broker)
            self.assertIsNotNone(pipe.contract_registry)
            self.assertIsNotNone(pipe.ooda)
            self.assertIsNotNone(pipe.file_writer)

            # LLM router has the agent config
            llm_cfg = pipe.router.get("analyst")
            self.assertEqual(llm_cfg.tier, "light")
            self.assertEqual(llm_cfg.temperature, 0.3)

    def test_build_manifest(self):
        from engine.core import AgenticPipeline, PipelineConfig, AgentDefinition

        with tempfile.TemporaryDirectory() as d:
            config = PipelineConfig(
                name="test", version="2.0.0",
                agents=[
                    AgentDefinition(name="a1", role="R", goal="G",
                                    llm_tier="premium", temperature=0.1),
                ],
                output_dir=d,
            )
            pipe = AgenticPipeline(config, tenant_id="t1")

            # Record some cost
            pipe.cost_meter.record("a1", "openai/gpt-4.1", 10000, 5000)

            manifest = pipe.build_manifest()
            self.assertEqual(manifest["manifest_version"], 2)
            self.assertEqual(manifest["pipeline"], "test")
            self.assertEqual(manifest["pipeline_version"], "2.0.0")
            self.assertIn("cost", manifest)
            self.assertGreater(manifest["cost"]["total_tokens"], 0)
            self.assertIn("a1", manifest["agents"])
            self.assertEqual(manifest["agents"]["a1"]["llm_config"]["temperature"], 0.1)




# ═══════════════════════════════════════════════════════════════
# (TestAPIApp removed — old engine/api.py stub was deleted in favor of engine/api_server.py)
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# FULL INTEGRATION: PIPELINE → RUNTIME → CONTRACTS → POLICY
# ═══════════════════════════════════════════════════════════════

class TestFullIntegration(unittest.TestCase):
    """End-to-end: define pipeline, run through runtime, validate contracts."""

    def test_end_to_end_pipeline(self):
        from engine.runtime import PipelineRuntime, StageDef, RetryPolicy
        from engine.contracts import ContractRegistry, StageContract
        from engine.policy import SandboxedFileWriter
        from engine.observability import AuditLog, Tracer

        with tempfile.TemporaryDirectory() as d:
            # Set up contracts
            contracts = ContractRegistry()
            contracts.set_output_dir(d)
            contracts.register(StageContract(
                name="research_contract", stage_name="research",
                required_files=["research/report.md"],
            ))
            contracts.register(StageContract(
                name="model_contract", stage_name="model",
                required_files=["model/financials.json"],
                rules=[{"rule": "file_min_size", "path": "model/financials.json",
                         "min_bytes": 10}],
            ))

            writer = SandboxedFileWriter(d)

            # Handlers that produce real artifacts
            def h_research(ctx):
                writer.write("research/report.md", "# Market Report\n" + "data " * 50)
                v = contracts.validate_stage("research")
                assert v.passed, f"Research contract failed: {v.errors}"
                return "research_done"

            def h_model(ctx):
                writer.write("model/financials.json",
                             json.dumps({"revenue": 5_000_000, "noi": 400_000}))
                v = contracts.validate_stage("model")
                assert v.passed, f"Model contract failed: {v.errors}"
                return "model_done"

            def h_report(ctx):
                writer.write("final_report.md", "# Final Report\nAll stages complete.")
                return "report_done"

            stages = [
                StageDef(name="research", handler="h_research"),
                StageDef(name="model", handler="h_model", depends_on=["research"]),
                StageDef(name="report", handler="h_report", depends_on=["model"]),
            ]

            rt = PipelineRuntime(
                stages=stages,
                handlers={
                    "h_research": h_research,
                    "h_model": h_model,
                    "h_report": h_report,
                },
                state={}, output_dir=d,
                tracer=Tracer(service_name="integration_test"),
                audit=AuditLog(),
            )

            results = rt.run()

            self.assertEqual(results["research"], "research_done")
            self.assertEqual(results["model"], "model_done")
            self.assertEqual(results["report"], "report_done")
            self.assertTrue(Path(d, "research/report.md").exists())
            self.assertTrue(Path(d, "model/financials.json").exists())
            self.assertTrue(Path(d, "final_report.md").exists())

    def test_imports_all_modules(self):
        """Verify all public API imports work."""
        import engine
        self.assertEqual(engine.__version__, "2.0.0")
        # Core classes exist
        self.assertTrue(hasattr(engine, 'PipelineRuntime'))
        self.assertTrue(hasattr(engine, 'PolicyBroker'))
        self.assertTrue(hasattr(engine, 'ContractRegistry'))
        self.assertTrue(hasattr(engine, 'SubprocessWorker'))
        self.assertTrue(hasattr(engine, 'AuthzEngine'))
        self.assertTrue(hasattr(engine, 'EvalSuite'))
        self.assertTrue(hasattr(engine, 'ConnectorRegistry'))
        self.assertTrue(hasattr(engine, 'AgenticPipeline'))
        self.assertTrue(hasattr(engine, 'OODALoop'))


if __name__ == "__main__":
    unittest.main()
