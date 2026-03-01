"""
engine.runtime — Durable Workflow Runtime
==========================================
AUDIT ITEM #1 (Impact: 10/10)

ZERO external dependencies — stdlib only.

Replaces the hard-coded sequential runner with:
  1. Declarative DAG (topological sort → parallel waves)
  2. Retry with exponential backoff + jitter
  3. Event journal (append-only JSONL) for deterministic replay
  4. Resume: replay completed stages, re-execute remaining
  5. Budget-aware scheduling: P0/P1/P2 smart skipping
  6. Atomic checkpoint after every stage
  7. Conditional execution (skip_if / run_if)
"""

from __future__ import annotations

import enum
import json
import random
import time
import traceback
import uuid
import ast
import operator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from engine.observability import Tracer, AuditLog


# ═══════════════════════════════════════════════════════════════
# SAFE CONDITION EVALUATOR — replaces eval() (P0.2 fix)
# ═══════════════════════════════════════════════════════════════

# Allowed AST node types for safe evaluation
_SAFE_COMPARE_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Is: operator.is_, ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_SAFE_BOOL_OPS = {ast.And: all, ast.Or: any}

_SAFE_UNARY_OPS = {ast.Not: operator.not_, ast.USub: operator.neg}


class SafeConditionEvaluator:
    """Evaluate boolean expressions WITHOUT eval().

    Supports:
      - Literals: True, False, None, numbers, strings
      - Variables: state, results (dict lookup)
      - Subscript: state["key"], results["stage"]
      - Attribute: state.field (only on provided scope dicts)
      - Comparisons: ==, !=, <, <=, >, >=, in, not in, is, is not
      - Boolean: and, or, not
      - Ternary: x if condition else y

    Rejects:
      - Function calls (no os.system(), __import__, etc.)
      - List/dict/set comprehensions
      - Lambda, yield, await
      - Assignments, augmented assignments
      - Starred expressions

    This is 100% safe against RCE. The AST is walked node-by-node
    with an explicit whitelist.
    """

    def __init__(self, scope: Dict[str, Any]):
        self._scope = scope

    def evaluate(self, expr: str) -> Any:
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return False
        return self._eval_node(tree.body)

    def _eval_node(self, node: ast.AST) -> Any:
        # Literals
        if isinstance(node, ast.Constant):
            return node.value

        # Variable lookup
        if isinstance(node, ast.Name):
            if node.id in self._scope:
                return self._scope[node.id]
            return None

        # Subscript: state["key"]
        if isinstance(node, ast.Subscript):
            obj = self._eval_node(node.value)
            if isinstance(node.slice, ast.Constant):
                key = node.slice.value
            else:
                key = self._eval_node(node.slice)
            if isinstance(obj, dict):
                return obj.get(key)
            return None

        # Attribute: state.field (safe — only on scope dicts, no dunders)
        if isinstance(node, ast.Attribute):
            # Block dunder access (__class__, __bases__, __import__, etc.)
            if node.attr.startswith("_"):
                return None
            obj = self._eval_node(node.value)
            if isinstance(obj, dict):
                return obj.get(node.attr)
            if hasattr(obj, node.attr):
                return getattr(obj, node.attr)
            return None

        # Boolean ops: and, or
        if isinstance(node, ast.BoolOp):
            op_fn = _SAFE_BOOL_OPS.get(type(node.op))
            if op_fn is None:
                return False
            values = [self._eval_node(v) for v in node.values]
            return op_fn(values)

        # Unary ops: not, -
        if isinstance(node, ast.UnaryOp):
            op_fn = _SAFE_UNARY_OPS.get(type(node.op))
            if op_fn is None:
                return False
            return op_fn(self._eval_node(node.operand))

        # Comparisons: ==, !=, <, >, in, etc.
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left)
            for op_node, comparator in zip(node.ops, node.comparators):
                op_fn = _SAFE_COMPARE_OPS.get(type(op_node))
                if op_fn is None:
                    return False
                right = self._eval_node(comparator)
                try:
                    if not op_fn(left, right):
                        return False
                except TypeError:
                    return False
                left = right
            return True

        # Ternary: x if cond else y
        if isinstance(node, ast.IfExp):
            if self._eval_node(node.test):
                return self._eval_node(node.body)
            return self._eval_node(node.orelse)

        # Tuple/List (for "x in [1, 2, 3]" patterns)
        if isinstance(node, (ast.Tuple, ast.List)):
            return [self._eval_node(e) for e in node.elts]

        # REJECT everything else (function calls, comprehensions, etc.)
        return False


def safe_eval_condition(expr, scope: Dict[str, Any]) -> bool:
    """Evaluate a condition expression safely.

    Accepts either:
      - str: evaluated via SafeConditionEvaluator (no eval())
      - callable: called with scope dict

    Returns bool.
    """
    if callable(expr):
        return bool(expr(scope))
    if isinstance(expr, str):
        return bool(SafeConditionEvaluator(scope).evaluate(expr))
    return False


# ═══════════════════════════════════════════════════════════════
# STAGE DEFINITION
# ═══════════════════════════════════════════════════════════════

class StageStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


@dataclass
class RetryPolicy:
    max_retries: int = 2
    base_delay_s: float = 5.0
    max_delay_s: float = 120.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_errors: List[str] = field(default_factory=lambda: [
        "context_length_exceeded", "rate_limit", "timeout",
        "connection", "502", "503", "529",
    ])


@dataclass
class StageDef:
    """Declarative stage definition for the pipeline DAG."""
    name: str
    handler: str                                     # Key in handler registry
    depends_on: List[str] = field(default_factory=list)
    priority: int = 0                                # 0=P0, 1=P1, 2=P2
    estimated_seconds: int = 600
    timeout_seconds: int = 1200
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    skip_if: Optional[str] = None
    run_if: Optional[str] = None
    description: str = ""
    tags: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# EVENT JOURNAL
# ═══════════════════════════════════════════════════════════════

class EventType(str, enum.Enum):
    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"
    PIPELINE_FAILED = "pipeline.failed"
    PIPELINE_RESUMED = "pipeline.resumed"
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    STAGE_SKIPPED = "stage.skipped"
    STAGE_RETRYING = "stage.retrying"
    CHECKPOINT_SAVED = "checkpoint.saved"
    HITL_REQUESTED = "hitl.requested"
    HITL_APPROVED = "hitl.approved"
    HITL_REJECTED = "hitl.rejected"
    OODA_ITERATION = "ooda.iteration"
    OODA_CONVERGED = "ooda.converged"
    OODA_EXHAUSTED = "ooda.exhausted"
    TOOL_INVOKED = "tool.invoked"
    TOOL_BLOCKED = "tool.blocked"
    BUDGET_WARNING = "budget.warning"


@dataclass
class PipelineEvent:
    event_id: str
    event_type: str
    timestamp: str
    pipeline_run_id: str
    stage_name: str = ""
    tenant_id: str = ""
    user_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: int = 0

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "pipeline_run_id": self.pipeline_run_id,
            "stage_name": self.stage_name,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class EventJournal:
    """Append-only event journal. Fsynced to JSONL."""

    def __init__(self, journal_path: Path, pipeline_run_id: str,
                 tenant_id: str = "", user_id: str = ""):
        self.journal_path = journal_path
        self.pipeline_run_id = pipeline_run_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._events: List[PipelineEvent] = []
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type, stage_name: str = "",
               data: Optional[Dict] = None, error: str = "",
               duration_ms: int = 0) -> PipelineEvent:
        et = event_type.value if isinstance(event_type, EventType) else str(event_type)
        event = PipelineEvent(
            event_id=uuid.uuid4().hex[:16], event_type=et,
            timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_run_id=self.pipeline_run_id,
            stage_name=stage_name, tenant_id=self.tenant_id,
            user_id=self.user_id, data=data or {},
            error=error, duration_ms=duration_ms,
        )
        self._events.append(event)
        try:
            import os as _os
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), separators=(",", ":"), default=str) + "\n")
                f.flush()
                _os.fsync(f.fileno())
        except OSError:
            pass
        return event

    @classmethod
    def load(cls, journal_path: Path) -> List[PipelineEvent]:
        events = []
        if not journal_path.exists():
            return events
        for line in journal_path.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                events.append(PipelineEvent(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    @property
    def events(self) -> List[PipelineEvent]:
        return list(self._events)

    def completed_stages(self) -> set:
        return {e.stage_name for e in self._events
                if e.event_type == EventType.STAGE_COMPLETED.value and e.stage_name}


# ═══════════════════════════════════════════════════════════════
# DAG RESOLVER
# ═══════════════════════════════════════════════════════════════

class DAGValidationError(Exception):
    pass


def resolve_dag(stages: List[StageDef]) -> List[List[str]]:
    """Topological sort → list of parallel waves. Raises on cycles."""
    stage_map = {s.name: s for s in stages}
    all_names = set(stage_map.keys())

    for s in stages:
        for dep in s.depends_on:
            if dep not in all_names:
                raise DAGValidationError(f"Stage '{s.name}' depends on '{dep}' which is not defined")

    in_degree: Dict[str, int] = {n: 0 for n in all_names}
    dependents: Dict[str, List[str]] = {n: [] for n in all_names}
    for s in stages:
        for dep in s.depends_on:
            dependents[dep].append(s.name)
            in_degree[s.name] += 1

    waves: List[List[str]] = []
    ready = sorted([n for n, d in in_degree.items() if d == 0])
    visited = 0

    while ready:
        waves.append(ready)
        visited += len(ready)
        next_ready = []
        for name in ready:
            for dep_name in dependents[name]:
                in_degree[dep_name] -= 1
                if in_degree[dep_name] == 0:
                    next_ready.append(dep_name)
        ready = sorted(next_ready)

    if visited != len(all_names):
        remaining = [n for n in all_names if in_degree[n] > 0]
        raise DAGValidationError(f"Cycle detected involving stages: {remaining}")

    return waves


# ═══════════════════════════════════════════════════════════════
# STAGE CONTEXT
# ═══════════════════════════════════════════════════════════════

@dataclass
class StageContext:
    """Passed to every stage handler."""
    stage_def: StageDef
    pipeline_run_id: str
    tenant_id: str
    user_id: str
    state: Any
    journal: EventJournal
    tracer: Tracer
    audit: AuditLog
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    attempt: int = 1
    max_attempts: int = 3
    budget_decision: str = "run"
    wave_index: int = 0


# ═══════════════════════════════════════════════════════════════
# BUDGET DECISIONS
# ═══════════════════════════════════════════════════════════════

class BudgetDecision(str, enum.Enum):
    RUN = "run"
    LITE = "lite"
    SKIP = "skip"

BUDGET_MARGINS = {"skip_p2": 0.5, "compress_p1": 0.8, "skip_p1": 0.3}

def budget_decision(stage: StageDef, remaining_s: float, remaining_p0_s: float) -> BudgetDecision:
    if stage.priority == 0:
        return BudgetDecision.RUN
    available = remaining_s - remaining_p0_s
    safety = available / stage.estimated_seconds if stage.estimated_seconds > 0 else 999
    if stage.priority == 2:
        return BudgetDecision.SKIP if safety < BUDGET_MARGINS["skip_p2"] else BudgetDecision.RUN
    if stage.priority == 1:
        if safety < BUDGET_MARGINS["skip_p1"]:
            return BudgetDecision.SKIP
        if safety < BUDGET_MARGINS["compress_p1"]:
            return BudgetDecision.LITE
        return BudgetDecision.RUN
    return BudgetDecision.RUN


# ═══════════════════════════════════════════════════════════════
# PIPELINE RUNTIME
# ═══════════════════════════════════════════════════════════════

def _calc_delay(attempt: int, policy: RetryPolicy) -> float:
    delay = min(policy.base_delay_s * (policy.backoff_factor ** (attempt - 1)), policy.max_delay_s)
    if policy.jitter:
        delay *= (0.5 + random.random())
    return delay

def _is_retryable(error: Exception, policy: RetryPolicy) -> bool:
    err_str = str(error).lower()
    return any(p in err_str for p in policy.retryable_errors)


class PipelineRuntime:
    """Durable DAG runtime with retry, replay, budget scheduling, and checkpointing."""

    def __init__(self, stages: List[StageDef],
                 handlers: Dict[str, Callable],
                 state: Any = None,
                 *, pipeline_run_id: str = "",
                 tenant_id: str = "", user_id: str = "",
                 output_dir: str = "./output",
                 total_budget_seconds: int = 3600,
                 tracer: Optional[Tracer] = None,
                 audit: Optional[AuditLog] = None,
                 use_subprocess: bool = False):
        self.stages = stages
        self.stage_map = {s.name: s for s in stages}
        self.handlers = handlers
        self.state = state or {}
        self.pipeline_run_id = pipeline_run_id or uuid.uuid4().hex[:12]
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.output_dir = Path(output_dir)
        self.total_budget_seconds = total_budget_seconds
        self.tracer = tracer or Tracer.noop()
        self.audit = audit or AuditLog.noop()
        self.use_subprocess = use_subprocess

        journal_path = self.output_dir / "events" / f"{self.pipeline_run_id}.jsonl"
        self.journal = EventJournal(journal_path, self.pipeline_run_id,
                                     tenant_id=tenant_id, user_id=user_id)

        self._statuses: Dict[str, StageStatus] = {s.name: StageStatus.PENDING for s in stages}
        self._results: Dict[str, Any] = {}
        self._timings: Dict[str, float] = {}
        self._start_time: float = 0.0
        self._completed: set = set()
        self._waves = resolve_dag(stages)

    @property
    def elapsed_s(self) -> float:
        return time.time() - self._start_time if self._start_time else 0.0

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.total_budget_seconds - self.elapsed_s)

    @property
    def remaining_p0_s(self) -> float:
        return sum(s.estimated_seconds for s in self.stages
                   if s.priority == 0 and s.name not in self._completed)

    def run(self) -> Dict[str, Any]:
        self._start_time = time.time()
        self.journal.append(EventType.PIPELINE_STARTED, data={
            "stages": [s.name for s in self.stages], "waves": self._waves,
            "budget_s": self.total_budget_seconds,
        })
        try:
            for wi, wave in enumerate(self._waves):
                self._execute_wave(wave, wi)
            self.journal.append(EventType.PIPELINE_COMPLETED, data={
                "elapsed_s": round(self.elapsed_s, 1),
                "completed": sorted(self._completed),
                "skipped": [n for n, s in self._statuses.items() if s == StageStatus.SKIPPED],
            })
        except Exception as e:
            self.journal.append(EventType.PIPELINE_FAILED, error=str(e)[:1000])
            raise
        return dict(self._results)

    def resume(self) -> Dict[str, Any]:
        events = EventJournal.load(self.journal.journal_path)
        self._completed = {e.stage_name for e in events
                           if e.event_type == EventType.STAGE_COMPLETED.value and e.stage_name}
        self._statuses = {s.name: (StageStatus.COMPLETED if s.name in self._completed
                                   else StageStatus.PENDING) for s in self.stages}
        self.journal.append(EventType.PIPELINE_RESUMED,
                            data={"already_completed": sorted(self._completed)})
        self._start_time = time.time()
        for wi, wave in enumerate(self._waves):
            remaining = [s for s in wave if s not in self._completed]
            if remaining:
                self._execute_wave(remaining, wi)
        self.journal.append(EventType.PIPELINE_COMPLETED,
                            data={"resumed": True, "elapsed_s": round(self.elapsed_s, 1)})
        return dict(self._results)

    def _execute_wave(self, names: List[str], wave_idx: int):
        if len(names) == 1:
            self._execute_stage(names[0], wave_idx)
            return
        if self.use_subprocess:
            # Subprocess mode: use threads to orchestrate, but each handler
            # runs in an isolated subprocess with hard kill capability
            import threading
            errors: Dict[str, Exception] = {}
            threads = []
            for n in names:
                t = threading.Thread(target=self._execute_stage_safe,
                                     args=(n, wave_idx, errors))
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            for n, exc in errors.items():
                if self.stage_map[n].priority == 0:
                    raise exc
        else:
            with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
                futures = {pool.submit(self._execute_stage, n, wave_idx): n for n in names}
                for future in as_completed(futures):
                    n = futures[future]
                    exc = future.exception()
                    if exc and self.stage_map[n].priority == 0:
                        raise exc

    def _execute_stage_safe(self, name: str, wave_idx: int,
                             errors: Dict[str, Exception]):
        """Thread-safe wrapper that captures exceptions for wave orchestration."""
        try:
            self._execute_stage(name, wave_idx)
        except Exception as e:
            errors[name] = e

    def _execute_stage(self, name: str, wave_idx: int):
        if name in self._completed:
            return
        stage = self.stage_map[name]

        # Conditional (SAFE — no eval())
        _scope = {"state": self.state, "results": self._results}
        if stage.skip_if:
            try:
                if safe_eval_condition(stage.skip_if, _scope):
                    self._skip(name, f"skip_if: {stage.skip_if}")
                    return
            except Exception:
                pass
        if stage.run_if:
            try:
                if not safe_eval_condition(stage.run_if, _scope):
                    self._skip(name, f"run_if not met")
                    return
            except Exception:
                pass

        bd = budget_decision(stage, self.remaining_s, self.remaining_p0_s)
        if bd == BudgetDecision.SKIP:
            self._skip(name, "budget pressure")
            return

        handler = self.handlers.get(stage.handler)
        if not handler:
            raise RuntimeError(f"No handler for '{stage.handler}'")

        ctx = StageContext(
            stage_def=stage, pipeline_run_id=self.pipeline_run_id,
            tenant_id=self.tenant_id, user_id=self.user_id,
            state=self.state, journal=self.journal,
            tracer=self.tracer, audit=self.audit,
            output_dir=self.output_dir,
            budget_decision=bd.value, wave_index=wave_idx,
        )

        max_attempts = stage.retry.max_retries + 1
        for attempt in range(1, max_attempts + 1):
            ctx.attempt = attempt
            ctx.max_attempts = max_attempts
            self._statuses[name] = StageStatus.RUNNING
            self.journal.append(EventType.STAGE_STARTED, stage_name=name,
                                data={"attempt": attempt, "budget": bd.value})
            t0 = time.time()
            try:
                with self.tracer.span(f"stage.{name}", attributes={
                    "stage.name": name, "stage.attempt": attempt, "stage.priority": stage.priority,
                }):
                    if self.use_subprocess:
                        result = self._run_handler_subprocess(handler, ctx, stage)
                    else:
                        result = handler(ctx)

                elapsed_ms = int((time.time() - t0) * 1000)
                self._statuses[name] = StageStatus.COMPLETED
                self._results[name] = result
                self._timings[name] = elapsed_ms / 1000
                self._completed.add(name)
                self.journal.append(EventType.STAGE_COMPLETED, stage_name=name,
                                    duration_ms=elapsed_ms, data={"attempt": attempt})
                self._checkpoint(name)
                return

            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                if attempt < max_attempts and _is_retryable(e, stage.retry):
                    delay = _calc_delay(attempt, stage.retry)
                    self._statuses[name] = StageStatus.RETRYING
                    self.journal.append(EventType.STAGE_RETRYING, stage_name=name,
                                        duration_ms=elapsed_ms,
                                        data={"attempt": attempt, "delay_s": round(delay, 1)},
                                        error=str(e)[:500])
                    time.sleep(delay)
                    continue
                self._statuses[name] = StageStatus.FAILED
                self.journal.append(EventType.STAGE_FAILED, stage_name=name,
                                    duration_ms=elapsed_ms,
                                    data={"attempt": attempt, "tb": traceback.format_exc()[:2000]},
                                    error=str(e)[:500])
                if stage.priority == 0:
                    raise
                return  # P1/P2 non-fatal

    def _skip(self, name: str, reason: str):
        self._statuses[name] = StageStatus.SKIPPED
        self._completed.add(name)
        self.journal.append(EventType.STAGE_SKIPPED, stage_name=name, data={"reason": reason})

    def _checkpoint(self, stage_name: str):
        ckpt_dir = self.output_dir / "checkpoints"
        ckpt_path = ckpt_dir / f"{self.pipeline_run_id}.json"
        ckpt_tmp = ckpt_path.with_suffix(".json.tmp")
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        try:
            state_dump = {}
            if hasattr(self.state, "__dict__"):
                state_dump = {k: v for k, v in self.state.__dict__.items()
                              if not k.startswith("_")}
            data = {
                "checkpoint_version": 2,
                "pipeline_run_id": self.pipeline_run_id,
                "tenant_id": self.tenant_id,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "last_completed": stage_name,
                "completed": sorted(self._completed),
                "statuses": {n: s.value for n, s in self._statuses.items()},
                "timings": self._timings,
                "budget": {
                    "total_s": self.total_budget_seconds,
                    "elapsed_s": round(self.elapsed_s, 1),
                    "remaining_s": round(self.remaining_s, 1),
                    "pct": round(self.elapsed_s / self.total_budget_seconds * 100
                                 if self.total_budget_seconds else 100, 1),
                },
                "state": state_dump,
            }
            ckpt_tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            ckpt_tmp.rename(ckpt_path)
            self.journal.append(EventType.CHECKPOINT_SAVED, stage_name=stage_name)
        except OSError:
            pass

    def _run_handler_subprocess(self, handler: Callable, ctx: 'StageContext',
                                 stage: StageDef) -> Any:
        """Execute handler in isolated subprocess with hard kill.

        P0.1 FIX: V1/V2 used Thread.join(timeout) which can't actually
        stop work. This uses SubprocessWorker which sends SIGKILL.

        The handler runs in a child process with resource limits.
        Non-picklable context fields (journal, tracer, audit) are stripped;
        the handler receives a lightweight ctx with data fields only.
        """
        from engine.workers import SubprocessWorker, ResourceQuota, WorkerStatus

        timeout = stage.timeout_seconds or int(self.remaining_s)
        quota = ResourceQuota(wall_time_seconds=max(timeout, 5))

        # Build lightweight picklable context data
        ctx_data = {
            "stage_name": stage.name,
            "pipeline_run_id": self.pipeline_run_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "output_dir": str(self.output_dir),
            "attempt": ctx.attempt,
            "max_attempts": ctx.max_attempts,
            "budget_decision": ctx.budget_decision,
            "wave_index": ctx.wave_index,
        }

        # Try passing full ctx (works if handler is top-level + ctx is picklable)
        # Fall back to lightweight dict if pickling fails
        try:
            worker = SubprocessWorker(quota)
            result = worker.run(handler, args=(ctx,), stage_name=stage.name)
        except Exception:
            # Ctx not picklable — use lightweight wrapper
            def _isolated_handler(h=handler, d=ctx_data):
                class _LightCtx:
                    pass
                lc = _LightCtx()
                for k, v in d.items():
                    setattr(lc, k, v)
                return h(lc)
            worker = SubprocessWorker(quota)
            result = worker.run(_isolated_handler, stage_name=stage.name)

        if result.status == WorkerStatus.COMPLETED.value:
            return result.output
        elif result.status == WorkerStatus.TIMEOUT.value:
            raise TimeoutError(
                f"Stage '{stage.name}' killed after {timeout}s "
                f"(SIGKILL — hard timeout)"
            )
        elif result.status == WorkerStatus.OOM.value:
            raise MemoryError(f"Stage '{stage.name}' exceeded memory limit")
        else:
            raise RuntimeError(
                f"Stage '{stage.name}' failed in subprocess: {result.error}"
            )

    def status(self) -> Dict:
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "elapsed_s": round(self.elapsed_s, 1),
            "remaining_s": round(self.remaining_s, 1),
            "stages": {n: s.value for n, s in self._statuses.items()},
            "completed": sorted(self._completed),
            "waves": self._waves,
        }

    def stage_status(self, name: str) -> StageStatus:
        return self._statuses.get(name, StageStatus.PENDING)
