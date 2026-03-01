"""
engine.observability — Tracing, Audit, and Cost Metering
=========================================================
AUDIT ITEM #7 (Impact: 9/10) + Immediate Fixes #1, #2

ZERO external dependencies — stdlib only.

Delivers:
  - EventEmitter: pluggable structured events (replaces bare print)
  - Tracer: OTel-compatible span API with noop fallback
  - AuditLog: immutable append-only log with fsync
  - CostMeter: real token/cost tracking with .record() hook
  - LLMRouter: tiered routing that propagates temp/max_tokens
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# STRUCTURED EVENT EMITTER
# ═══════════════════════════════════════════════════════════════

class EventEmitter:
    """Structured event emission with pluggable backends.
    Replaces V1's bare print(##EV:...##) pattern.
    """

    def __init__(self, backends: Optional[List[str]] = None):
        self._backends = backends or ["stdout"]
        self._handlers: List[Callable] = []

    def add_handler(self, fn: Callable):
        self._handlers.append(fn)

    def emit(self, event_type: str, **data):
        payload = {
            "t": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        for handler in self._handlers:
            try:
                handler(payload)
            except Exception:
                pass
        if "stdout" in self._backends:
            print(f"##EV:{json.dumps(payload, separators=(',',':'), default=str)}##", flush=True)

    @classmethod
    def noop(cls) -> "EventEmitter":
        return cls(backends=[])


# ═══════════════════════════════════════════════════════════════
# OTEL-COMPATIBLE TRACER
# ═══════════════════════════════════════════════════════════════

@dataclass
class SpanData:
    """OTel-compliant span with semantic conventions.

    P1.3 FIX: Now conforms to OpenTelemetry semantic conventions:
      - service.name, service.version, deployment.environment
      - span.kind (internal, server, client, producer, consumer)
      - Proper status codes (STATUS_OK, STATUS_ERROR, STATUS_UNSET)
      - W3C traceparent format for distributed context
    """
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    name: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "STATUS_UNSET"
    status_message: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    kind: str = "INTERNAL"    # INTERNAL, SERVER, CLIENT, PRODUCER, CONSUMER
    events: List[Dict] = field(default_factory=list)
    # OTel resource attributes (set by Tracer)
    resource: Dict[str, str] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        if self.end_time and self.start_time:
            return int((self.end_time - self.start_time) * 1000)
        return 0

    @property
    def traceparent(self) -> str:
        """W3C traceparent header for distributed context propagation."""
        flags = "01"  # sampled
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    def add_event(self, name: str, attributes: Optional[Dict] = None):
        """Add a span event (OTel convention)."""
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def to_otel_dict(self) -> Dict:
        """Export as OTel-compatible dict (for OTLP JSON exporter)."""
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "name": self.name,
            "kind": f"SPAN_KIND_{self.kind}",
            "startTimeUnixNano": int(self.start_time * 1e9) if self.start_time else 0,
            "endTimeUnixNano": int(self.end_time * 1e9) if self.end_time else 0,
            "status": {"code": self.status, "message": self.status_message},
            "attributes": [
                {"key": k, "value": {"stringValue": str(v)}}
                for k, v in self.attributes.items()
            ],
            "events": [
                {"name": e["name"], "timeUnixNano": int(e["timestamp"] * 1e9),
                 "attributes": [{"key": k, "value": {"stringValue": str(v)}}
                                for k, v in e.get("attributes", {}).items()]}
                for e in self.events
            ],
            "resource": {
                "attributes": [
                    {"key": k, "value": {"stringValue": v}}
                    for k, v in self.resource.items()
                ]
            },
        }


class Tracer:
    """OTel-compliant span tracer with JSONL persistence.

    P1.3 FIX: Now includes:
      - Resource attributes (service.name, service.version, deployment.environment)
      - Semantic span kinds
      - W3C traceparent propagation
      - OTel-compatible export format
      - Span events for fine-grained annotation
    """

    def __init__(self, service_name: str = "agentic-engine",
                 service_version: str = "2.0.0",
                 environment: str = "development",
                 spans_path: Optional[Path] = None):
        self.service_name = service_name
        self._spans: List[SpanData] = []
        self._spans_path = spans_path
        self._trace_id = uuid.uuid4().hex[:32]
        self._span_stack: List[str] = []
        self._resource = {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
            "telemetry.sdk.name": "agentic-engine",
            "telemetry.sdk.language": "python",
        }

    @contextmanager
    def span(self, name: str, attributes: Optional[Dict] = None,
             kind: str = "INTERNAL"):
        span_id = uuid.uuid4().hex[:16]
        parent_id = self._span_stack[-1] if self._span_stack else ""
        self._span_stack.append(span_id)
        sd = SpanData(
            trace_id=self._trace_id, span_id=span_id,
            parent_span_id=parent_id, name=name,
            start_time=time.time(), attributes=attributes or {},
            kind=kind, resource=dict(self._resource),
        )
        try:
            yield sd
            sd.status = "STATUS_OK"
        except Exception as e:
            sd.status = "STATUS_ERROR"
            sd.status_message = str(e)[:500]
            sd.attributes["error.type"] = type(e).__name__
            sd.attributes["error.message"] = str(e)[:500]
            raise
        finally:
            sd.end_time = time.time()
            self._span_stack.pop()
            self._spans.append(sd)
            self._persist_span(sd)

    def _persist_span(self, sd: SpanData):
        if not self._spans_path:
            return
        try:
            self._spans_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._spans_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "traceId": sd.trace_id, "spanId": sd.span_id,
                    "parentSpanId": sd.parent_span_id, "name": sd.name,
                    "kind": sd.kind, "status": sd.status,
                    "startTimeUnixNano": int(sd.start_time * 1e9),
                    "endTimeUnixNano": int(sd.end_time * 1e9),
                    "durationMs": sd.duration_ms,
                    "attributes": sd.attributes,
                    "events": sd.events,
                    "resource": sd.resource,
                }, separators=(",", ":"), default=str) + "\n")
        except OSError:
            pass

    @property
    def traceparent(self) -> str:
        """Current W3C traceparent for distributed propagation."""
        span_id = self._span_stack[-1] if self._span_stack else "0" * 16
        return f"00-{self._trace_id}-{span_id}-01"

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def spans(self) -> List[SpanData]:
        return list(self._spans)

    @classmethod
    def noop(cls) -> "Tracer":
        return cls(service_name="noop")


# ═══════════════════════════════════════════════════════════════
# IMMUTABLE AUDIT LOG
# ═══════════════════════════════════════════════════════════════

@dataclass
class AuditEntry:
    entry_id: str
    timestamp: str
    tenant_id: str
    user_id: str
    action: str
    resource: str
    outcome: str
    details: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    pipeline_run_id: str = ""


class AuditLog:
    """Append-only audit log with fsync. No update/delete."""

    def __init__(self, log_path: Optional[Path] = None,
                 tenant_id: str = "", pipeline_run_id: str = ""):
        self._log_path = log_path
        self._tenant_id = tenant_id
        self._pipeline_run_id = pipeline_run_id
        self._entries: List[AuditEntry] = []

    def log(self, action: str, resource: str, outcome: str,
            user_id: str = "", details: Optional[Dict] = None,
            trace_id: str = "") -> AuditEntry:
        entry = AuditEntry(
            entry_id=uuid.uuid4().hex[:16],
            timestamp=datetime.now(timezone.utc).isoformat(),
            tenant_id=self._tenant_id, user_id=user_id,
            action=action, resource=resource, outcome=outcome,
            details=details or {}, trace_id=trace_id,
            pipeline_run_id=self._pipeline_run_id,
        )
        self._entries.append(entry)
        self._persist(entry)
        return entry

    def _persist(self, entry: AuditEntry):
        if not self._log_path:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "id": entry.entry_id, "ts": entry.timestamp,
                    "tenant": entry.tenant_id, "user": entry.user_id,
                    "action": entry.action, "resource": entry.resource,
                    "outcome": entry.outcome, "details": entry.details,
                    "trace_id": entry.trace_id, "run_id": entry.pipeline_run_id,
                }, separators=(",", ":"), default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

    @property
    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    @classmethod
    def noop(cls) -> "AuditLog":
        return cls()


# ═══════════════════════════════════════════════════════════════
# COST METERING — Actually wired now
# ═══════════════════════════════════════════════════════════════

# Pricing per 1M tokens
DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    "openai/gpt-4.1":      {"in": 2.00,  "out": 8.00},
    "openai/gpt-4.1-mini": {"in": 0.40,  "out": 1.60},
    "openai/gpt-4o":       {"in": 2.50,  "out": 10.00},
    "openai/gpt-4o-mini":  {"in": 0.15,  "out": 0.60},
    "anthropic/claude-sonnet-4-20250514": {"in": 3.00, "out": 15.00},
}


@dataclass
class UsageRecord:
    timestamp: str
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    pipeline_run_id: str = ""
    tenant_id: str = ""
    stage_name: str = ""
    latency_ms: int = 0


class BudgetExceededError(Exception):
    """Raised when cost enforcement blocks further execution."""
    def __init__(self, scope: str, limit: float, current: float):
        self.scope = scope
        self.limit = limit
        self.current = current
        super().__init__(
            f"Budget exceeded ({scope}): ${current:.4f} / ${limit:.4f}"
        )


class CostMeter:
    """Real cost tracking with circuit breaker enforcement.

    P1.4 FIX: Now enforces budgets mid-flight, not just records them.
      - Pipeline-level budget limit (hard stop)
      - Per-stage budget limits
      - Token runaway detection (max tokens per single call)
      - Circuit breaker: tripped → all subsequent calls blocked
      - Pre-flight enforce_budget() check before LLM calls
    """

    def __init__(self, pricing: Optional[Dict] = None,
                 budget_limit_usd: float = 50.0,
                 tenant_id: str = "", pipeline_run_id: str = "",
                 ledger_path: Optional[Path] = None,
                 stage_budgets: Optional[Dict[str, float]] = None,
                 max_tokens_per_call: int = 500_000):
        self.pricing = pricing or DEFAULT_PRICING
        self.budget_limit_usd = budget_limit_usd
        self.tenant_id = tenant_id
        self.pipeline_run_id = pipeline_run_id
        self._ledger_path = ledger_path
        self._records: List[UsageRecord] = []
        self._alert_handlers: List[Callable] = []
        self._stage_budgets = stage_budgets or {}  # stage_name → max_usd
        self._max_tokens_per_call = max_tokens_per_call
        self._circuit_open = False
        self._circuit_reason = ""

    def on_budget_alert(self, handler: Callable):
        self._alert_handlers.append(handler)

    @property
    def circuit_open(self) -> bool:
        """True if circuit breaker has tripped (all further calls blocked)."""
        return self._circuit_open

    def enforce_budget(self, stage_name: str = "",
                       estimated_tokens: int = 0):
        """Pre-flight check before making an LLM call.

        Call this BEFORE sending to the LLM. Raises BudgetExceededError
        if the budget would be breached.

        Usage:
            meter.enforce_budget(stage_name="research", estimated_tokens=10000)
            # If no exception, safe to proceed
            response = llm.call(...)
            meter.record(...)
        """
        if self._circuit_open:
            raise BudgetExceededError(
                f"circuit_breaker ({self._circuit_reason})",
                self.budget_limit_usd, self.total_cost_usd,
            )

        # Pipeline budget
        if self.total_cost_usd >= self.budget_limit_usd:
            self._trip_circuit("pipeline_budget_exceeded")
            raise BudgetExceededError(
                "pipeline", self.budget_limit_usd, self.total_cost_usd
            )

        # Stage budget
        if stage_name and stage_name in self._stage_budgets:
            stage_cost = self.stage_cost_usd(stage_name)
            stage_limit = self._stage_budgets[stage_name]
            if stage_cost >= stage_limit:
                raise BudgetExceededError(
                    f"stage:{stage_name}", stage_limit, stage_cost
                )

        # Token runaway check
        if estimated_tokens > self._max_tokens_per_call:
            raise BudgetExceededError(
                "token_runaway",
                float(self._max_tokens_per_call),
                float(estimated_tokens),
            )

    def record(self, agent_name: str, model: str,
               input_tokens: int, output_tokens: int,
               stage_name: str = "", latency_ms: int = 0) -> UsageRecord:
        """Record an LLM call and check budgets post-hoc.

        If budget is exceeded after this call, trips the circuit breaker.
        """
        p = self.pricing.get(model, {})
        cost = (input_tokens / 1e6) * p.get("in", 5.0) + \
               (output_tokens / 1e6) * p.get("out", 15.0)

        rec = UsageRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_name=agent_name, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            pipeline_run_id=self.pipeline_run_id,
            tenant_id=self.tenant_id,
            stage_name=stage_name, latency_ms=latency_ms,
        )
        self._records.append(rec)
        self._persist(rec)

        # Post-hoc budget check + alert
        total = self.total_cost_usd
        if total > self.budget_limit_usd:
            self._trip_circuit("budget_exceeded_post_call")
            for h in self._alert_handlers:
                try:
                    h("exceeded", total)
                except Exception:
                    pass
        elif total > self.budget_limit_usd * 0.8:
            for h in self._alert_handlers:
                try:
                    h("warning", total)
                except Exception:
                    pass
        return rec

    def _trip_circuit(self, reason: str):
        """Trip the circuit breaker — blocks all future calls."""
        self._circuit_open = True
        self._circuit_reason = reason

    def reset_circuit(self):
        """Manual circuit breaker reset (requires human/admin action)."""
        self._circuit_open = False
        self._circuit_reason = ""

    def stage_cost_usd(self, stage_name: str) -> float:
        """Total cost for a specific stage."""
        return round(sum(r.cost_usd for r in self._records
                         if r.stage_name == stage_name), 4)

    def _persist(self, rec: UsageRecord):
        if not self._ledger_path:
            return
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": rec.timestamp, "agent": rec.agent_name,
                    "model": rec.model, "in": rec.input_tokens,
                    "out": rec.output_tokens, "cost": rec.cost_usd,
                    "run": rec.pipeline_run_id, "tenant": rec.tenant_id,
                    "stage": rec.stage_name, "latency_ms": rec.latency_ms,
                }, separators=(",", ":")) + "\n")
        except OSError:
            pass

    @property
    def total_cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self._records), 4)

    @property
    def total_tokens(self) -> int:
        return sum(r.input_tokens + r.output_tokens for r in self._records)

    def per_agent_summary(self) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        for r in self._records:
            if r.agent_name not in out:
                out[r.agent_name] = {"model": r.model, "calls": 0,
                    "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            s = out[r.agent_name]
            s["calls"] += 1
            s["input_tokens"] += r.input_tokens
            s["output_tokens"] += r.output_tokens
            s["cost_usd"] = round(s["cost_usd"] + r.cost_usd, 4)
        return out

    def per_stage_summary(self) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        for r in self._records:
            key = r.stage_name or "unknown"
            if key not in out:
                out[key] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            s = out[key]
            s["calls"] += 1
            s["tokens"] += r.input_tokens + r.output_tokens
            s["cost_usd"] = round(s["cost_usd"] + r.cost_usd, 4)
        return out

    def billing_summary(self) -> Dict:
        return {
            "tenant_id": self.tenant_id,
            "pipeline_run_id": self.pipeline_run_id,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "budget_limit_usd": self.budget_limit_usd,
            "budget_remaining_usd": round(self.budget_limit_usd - self.total_cost_usd, 4),
            "per_agent": self.per_agent_summary(),
            "per_stage": self.per_stage_summary(),
            "records_count": len(self._records),
        }


# ═══════════════════════════════════════════════════════════════
# LLM ROUTER — Actually propagates config now
# ═══════════════════════════════════════════════════════════════

@dataclass
class LLMConfig:
    model: str
    tier: str = "heavy"
    temperature: float = 0.5
    max_tokens: int = 128_000
    top_p: float = 1.0
    timeout_s: int = 300


class LLMRouter:
    """Tiered LLM routing. register() returns full LLMConfig used by agent factory.
    AUDIT FIX #1: temp/max_tokens now stored AND propagated.
    """

    def __init__(self):
        self._configs: Dict[str, LLMConfig] = {}
        self.premium_model = os.getenv("LLM_PREMIUM", "openai/gpt-4.1")
        self.heavy_model = os.getenv("LLM_HEAVY", "openai/gpt-4.1")
        self.light_model = os.getenv("LLM_LIGHT", "openai/gpt-4.1-mini")

    def register(self, agent_name: str, tier: str = "heavy",
                 temperature: float = 0.5, max_tokens: int = 128_000,
                 **kwargs) -> LLMConfig:
        model_map = {"premium": self.premium_model,
                      "heavy": self.heavy_model,
                      "light": self.light_model}
        config = LLMConfig(
            model=model_map.get(tier, self.heavy_model),
            tier=tier, temperature=temperature,
            max_tokens=max_tokens, **kwargs,
        )
        self._configs[agent_name] = config
        return config

    def get(self, agent_name: str) -> LLMConfig:
        return self._configs.get(agent_name, LLMConfig(model=self.heavy_model))

    def all_configs(self) -> Dict[str, LLMConfig]:
        return dict(self._configs)
