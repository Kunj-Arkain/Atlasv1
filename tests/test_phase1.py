"""
Phase 1 Test Suite — stdlib unittest (no pytest required)
Run: cd /home/claude/phase1 && python -m unittest tests.test_phase1 -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure engine is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDAGResolution(unittest.TestCase):

    def test_linear_chain(self):
        from engine.runtime import StageDef, resolve_dag
        stages = [StageDef(name="a", handler="h"),
                  StageDef(name="b", handler="h", depends_on=["a"]),
                  StageDef(name="c", handler="h", depends_on=["b"])]
        self.assertEqual(resolve_dag(stages), [["a"], ["b"], ["c"]])

    def test_diamond_dag(self):
        from engine.runtime import StageDef, resolve_dag
        stages = [StageDef(name="init", handler="h"),
                  StageDef(name="research", handler="h", depends_on=["init"]),
                  StageDef(name="finance", handler="h", depends_on=["init"]),
                  StageDef(name="assemble", handler="h", depends_on=["research", "finance"])]
        waves = resolve_dag(stages)
        self.assertEqual(waves[0], ["init"])
        self.assertEqual(set(waves[1]), {"finance", "research"})
        self.assertEqual(waves[2], ["assemble"])

    def test_all_independent(self):
        from engine.runtime import StageDef, resolve_dag
        stages = [StageDef(name="a", handler="h"),
                  StageDef(name="b", handler="h"),
                  StageDef(name="c", handler="h")]
        waves = resolve_dag(stages)
        self.assertEqual(len(waves), 1)
        self.assertEqual(set(waves[0]), {"a", "b", "c"})

    def test_cycle_detected(self):
        from engine.runtime import StageDef, resolve_dag, DAGValidationError
        stages = [StageDef(name="a", handler="h", depends_on=["c"]),
                  StageDef(name="b", handler="h", depends_on=["a"]),
                  StageDef(name="c", handler="h", depends_on=["b"])]
        with self.assertRaises(DAGValidationError):
            resolve_dag(stages)

    def test_missing_dep(self):
        from engine.runtime import StageDef, resolve_dag, DAGValidationError
        stages = [StageDef(name="a", handler="h", depends_on=["ghost"])]
        with self.assertRaises(DAGValidationError):
            resolve_dag(stages)

    def test_complex_6_stage(self):
        from engine.runtime import StageDef, resolve_dag
        stages = [StageDef(name="init", handler="h"),
                  StageDef(name="da", handler="h", depends_on=["init"]),
                  StageDef(name="db", handler="h", depends_on=["init"]),
                  StageDef(name="model", handler="h", depends_on=["da", "db"]),
                  StageDef(name="review", handler="h", depends_on=["model"]),
                  StageDef(name="export", handler="h", depends_on=["model"])]
        waves = resolve_dag(stages)
        self.assertEqual(waves[0], ["init"])
        self.assertEqual(set(waves[1]), {"da", "db"})
        self.assertEqual(waves[2], ["model"])
        self.assertEqual(set(waves[3]), {"export", "review"})


class TestBudgetDecision(unittest.TestCase):

    def test_p0_always_runs(self):
        from engine.runtime import StageDef, BudgetDecision, budget_decision
        s = StageDef(name="x", handler="h", priority=0, estimated_seconds=600)
        self.assertEqual(budget_decision(s, 100, 50), BudgetDecision.RUN)

    def test_p2_skipped(self):
        from engine.runtime import StageDef, BudgetDecision, budget_decision
        s = StageDef(name="x", handler="h", priority=2, estimated_seconds=600)
        self.assertEqual(budget_decision(s, 700, 500), BudgetDecision.SKIP)

    def test_p2_runs_with_room(self):
        from engine.runtime import StageDef, BudgetDecision, budget_decision
        s = StageDef(name="x", handler="h", priority=2, estimated_seconds=600)
        self.assertEqual(budget_decision(s, 3000, 500), BudgetDecision.RUN)

    def test_p1_skipped_when_tight(self):
        from engine.runtime import StageDef, BudgetDecision, budget_decision
        s = StageDef(name="x", handler="h", priority=1, estimated_seconds=600)
        self.assertEqual(budget_decision(s, 650, 500), BudgetDecision.SKIP)


class TestEventJournal(unittest.TestCase):

    def test_append_and_load(self):
        from engine.runtime import EventJournal, EventType
        with tempfile.TemporaryDirectory() as d:
            j = EventJournal(Path(d) / "e.jsonl", "run1")
            j.append(EventType.STAGE_STARTED, stage_name="init")
            j.append(EventType.STAGE_COMPLETED, stage_name="init", duration_ms=150)
            j.append(EventType.STAGE_FAILED, stage_name="x", error="boom")
            self.assertEqual(len(j.events), 3)
            loaded = EventJournal.load(Path(d) / "e.jsonl")
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[0].stage_name, "init")
            self.assertEqual(loaded[2].error, "boom")

    def test_completed_stages(self):
        from engine.runtime import EventJournal, EventType
        with tempfile.TemporaryDirectory() as d:
            j = EventJournal(Path(d) / "e.jsonl", "run1")
            j.append(EventType.STAGE_COMPLETED, stage_name="a")
            j.append(EventType.STAGE_COMPLETED, stage_name="b")
            j.append(EventType.STAGE_FAILED, stage_name="c")
            self.assertEqual(j.completed_stages(), {"a", "b"})


class TestPipelineRuntime(unittest.TestCase):

    def _rt(self, tmpdir, stages, handlers):
        from engine.runtime import PipelineRuntime
        return PipelineRuntime(stages=stages, handlers=handlers,
                               state={}, output_dir=tmpdir, total_budget_seconds=300)

    def test_linear_pipeline(self):
        from engine.runtime import StageDef
        log = []
        stages = [StageDef(name="a", handler="h"),
                  StageDef(name="b", handler="h", depends_on=["a"]),
                  StageDef(name="c", handler="h", depends_on=["b"])]
        def h(ctx):
            log.append(ctx.stage_def.name)
            return f"{ctx.stage_def.name}_ok"
        with tempfile.TemporaryDirectory() as d:
            results = self._rt(d, stages, {"h": h}).run()
        self.assertEqual(log, ["a", "b", "c"])
        self.assertEqual(results["c"], "c_ok")

    def test_parallel_wave(self):
        from engine.runtime import StageDef
        log = []
        stages = [StageDef(name="a", handler="h"),
                  StageDef(name="b", handler="h"),
                  StageDef(name="c", handler="h")]
        def h(ctx):
            log.append(ctx.stage_def.name)
            return "ok"
        with tempfile.TemporaryDirectory() as d:
            results = self._rt(d, stages, {"h": h}).run()
        self.assertEqual(set(log), {"a", "b", "c"})
        self.assertEqual(len(results), 3)

    def test_retry_on_failure(self):
        from engine.runtime import StageDef, RetryPolicy
        attempts = [0]
        def flaky(ctx):
            attempts[0] += 1
            if attempts[0] < 3:
                raise ConnectionError("rate_limit")
            return "ok"
        stages = [StageDef(name="f", handler="h",
                           retry=RetryPolicy(max_retries=3, base_delay_s=0.01))]
        with tempfile.TemporaryDirectory() as d:
            results = self._rt(d, stages, {"h": flaky}).run()
        self.assertEqual(results["f"], "ok")
        self.assertEqual(attempts[0], 3)

    def test_p0_failure_fatal(self):
        from engine.runtime import StageDef, RetryPolicy
        stages = [StageDef(name="x", handler="h", priority=0,
                           retry=RetryPolicy(max_retries=0))]
        def bad(ctx):
            raise ValueError("fatal")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                self._rt(d, stages, {"h": bad}).run()

    def test_p2_failure_nonfatal(self):
        from engine.runtime import StageDef, RetryPolicy
        stages = [StageDef(name="opt", handler="h_bad", priority=2,
                           retry=RetryPolicy(max_retries=0)),
                  StageDef(name="next", handler="h_ok", depends_on=["opt"])]
        def h_bad(ctx):
            raise RuntimeError("meh")
        def h_ok(ctx):
            return "fine"
        with tempfile.TemporaryDirectory() as d:
            results = self._rt(d, stages, {"h_bad": h_bad, "h_ok": h_ok}).run()
        self.assertIn("next", results)

    def test_skip_if(self):
        from engine.runtime import StageDef
        stages = [StageDef(name="a", handler="h"),
                  StageDef(name="s", handler="h", skip_if="True", depends_on=["a"])]
        def h(ctx):
            return "done"
        with tempfile.TemporaryDirectory() as d:
            rt = self._rt(d, stages, {"h": h})
            results = rt.run()
        self.assertIn("a", results)
        self.assertNotIn("s", results)
        self.assertEqual(rt.stage_status("s").value, "skipped")

    def test_checkpoint_written(self):
        from engine.runtime import StageDef
        stages = [StageDef(name="only", handler="h")]
        with tempfile.TemporaryDirectory() as d:
            rt = self._rt(d, stages, {"h": lambda ctx: "ok"})
            rt.run()
            ckpt = Path(d) / "checkpoints" / f"{rt.pipeline_run_id}.json"
            self.assertTrue(ckpt.exists())
            data = json.loads(ckpt.read_text())
            self.assertEqual(data["checkpoint_version"], 2)
            self.assertEqual(data["last_completed"], "only")

    def test_journal_events(self):
        from engine.runtime import StageDef
        stages = [StageDef(name="only", handler="h")]
        with tempfile.TemporaryDirectory() as d:
            rt = self._rt(d, stages, {"h": lambda ctx: "ok"})
            rt.run()
            jp = Path(d) / "events" / f"{rt.pipeline_run_id}.jsonl"
            events = [json.loads(l) for l in jp.read_text().strip().split("\n")]
            types = [e["event_type"] for e in events]
            self.assertIn("pipeline.started", types)
            self.assertIn("stage.started", types)
            self.assertIn("stage.completed", types)
            self.assertIn("checkpoint.saved", types)
            self.assertIn("pipeline.completed", types)

    def test_resume_skips_completed(self):
        from engine.runtime import StageDef, PipelineRuntime
        log = []
        stages_12 = [StageDef(name="a", handler="h"),
                     StageDef(name="b", handler="h", depends_on=["a"])]
        stages_all = stages_12 + [StageDef(name="c", handler="h", depends_on=["b"])]
        def h(ctx):
            log.append(ctx.stage_def.name)
            return "ok"
        with tempfile.TemporaryDirectory() as d:
            PipelineRuntime(stages=stages_12, handlers={"h": h},
                            state={}, output_dir=d, pipeline_run_id="res1").run()
            self.assertEqual(log, ["a", "b"])
            log.clear()
            PipelineRuntime(stages=stages_all, handlers={"h": h},
                            state={}, output_dir=d, pipeline_run_id="res1").resume()
            self.assertEqual(log, ["c"])


class TestEventEmitter(unittest.TestCase):

    def test_handler_receives(self):
        from engine.observability import EventEmitter
        captured = []
        e = EventEmitter(backends=[])
        e.add_handler(lambda ev: captured.append(ev))
        e.emit("test", k="v", n=42)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["t"], "test")
        self.assertEqual(captured[0]["k"], "v")

    def test_handler_error_safe(self):
        from engine.observability import EventEmitter
        e = EventEmitter(backends=[])
        e.add_handler(lambda ev: 1 / 0)
        e.emit("test")  # Should not raise


class TestTracer(unittest.TestCase):

    def test_span_duration(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("op", attributes={"k": "v"}):
            time.sleep(0.01)
        self.assertEqual(len(t._spans), 1)
        self.assertGreaterEqual(t._spans[0].duration_ms, 10)
        self.assertEqual(t._spans[0].status, "STATUS_OK")

    def test_span_error(self):
        from engine.observability import Tracer
        t = Tracer()
        with self.assertRaises(ValueError):
            with t.span("bad"):
                raise ValueError("boom")
        self.assertEqual(t._spans[0].status, "STATUS_ERROR")
        self.assertEqual(t._spans[0].attributes["error.type"], "ValueError")

    def test_nested_spans(self):
        from engine.observability import Tracer
        t = Tracer()
        with t.span("outer"):
            with t.span("inner"):
                pass
        inner, outer = t._spans
        self.assertEqual(inner.parent_span_id, outer.span_id)

    def test_persist_to_file(self):
        from engine.observability import Tracer
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spans.jsonl"
            t = Tracer(spans_path=p)
            with t.span("op"):
                pass
            self.assertTrue(p.exists())
            data = json.loads(p.read_text().strip())
            self.assertEqual(data["name"], "op")


class TestAuditLog(unittest.TestCase):

    def test_append_persist(self):
        from engine.observability import AuditLog
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "audit.jsonl"
            log = AuditLog(log_path=p, tenant_id="t1", pipeline_run_id="r1")
            log.log("stage.done", "stage:init", "success", user_id="u1", details={"x": 1})
            log.log("tool.used", "tool:write", "success")
            self.assertEqual(len(log.entries), 2)
            lines = p.read_text().strip().split("\n")
            self.assertEqual(len(lines), 2)
            row = json.loads(lines[0])
            self.assertEqual(row["action"], "stage.done")
            self.assertEqual(row["tenant"], "t1")


class TestCostMeter(unittest.TestCase):

    def test_records_usage(self):
        from engine.observability import CostMeter
        m = CostMeter(budget_limit_usd=100)
        m.record("underwriter", "openai/gpt-4.1", 50000, 10000, stage_name="fin")
        m.record("analyst", "openai/gpt-4.1-mini", 30000, 5000, stage_name="res")
        self.assertEqual(m.total_tokens, 95000)
        self.assertGreater(m.total_cost_usd, 0)
        s = m.per_agent_summary()
        self.assertIn("underwriter", s)
        self.assertEqual(s["underwriter"]["calls"], 1)

    def test_budget_alert(self):
        from engine.observability import CostMeter
        alerts = []
        m = CostMeter(budget_limit_usd=0.001)
        m.on_budget_alert(lambda lvl, cost: alerts.append((lvl, cost)))
        m.record("a", "openai/gpt-4.1", 100000, 50000)
        self.assertGreaterEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], "exceeded")

    def test_billing_summary(self):
        from engine.observability import CostMeter
        m = CostMeter(tenant_id="ws1", pipeline_run_id="r1", budget_limit_usd=50)
        m.record("a", "openai/gpt-4.1", 10000, 5000)
        b = m.billing_summary()
        self.assertEqual(b["tenant_id"], "ws1")
        self.assertEqual(b["records_count"], 1)
        self.assertEqual(b["total_tokens"], 15000)

    def test_ledger_persisted(self):
        from engine.observability import CostMeter
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ledger.jsonl"
            m = CostMeter(ledger_path=p)
            m.record("a", "openai/gpt-4.1", 1000, 500)
            m.record("b", "openai/gpt-4.1-mini", 2000, 1000)
            lines = p.read_text().strip().split("\n")
            self.assertEqual(len(lines), 2)


class TestLLMRouter(unittest.TestCase):

    def test_register_returns_config(self):
        from engine.observability import LLMRouter
        r = LLMRouter()
        c = r.register("uw", tier="premium", temperature=0.1, max_tokens=64000)
        self.assertEqual(c.tier, "premium")
        self.assertEqual(c.temperature, 0.1)
        self.assertEqual(c.max_tokens, 64000)
        self.assertEqual(c.model, r.premium_model)

    def test_get_registered(self):
        from engine.observability import LLMRouter
        r = LLMRouter()
        r.register("x", tier="light", temperature=0.7)
        c = r.get("x")
        self.assertEqual(c.tier, "light")
        self.assertEqual(c.temperature, 0.7)

    def test_get_unknown_default(self):
        from engine.observability import LLMRouter
        c = LLMRouter().get("ghost")
        self.assertEqual(c.tier, "heavy")


if __name__ == "__main__":
    unittest.main()
