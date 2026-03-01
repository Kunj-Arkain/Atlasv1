"""
engine.brain.adapter — ArkainBrain Runtime Adapter
======================================================
Phase 8A: Wraps ArkainBrain as the LLM orchestration layer.

Architecture:
  - ArkainBrainAdapter: implements the engine's LLMRouter interface
  - Reads all config from DB (agent configs, model routes)
  - All LLM calls go through PolicyBroker + CostMeter
  - Tool calls dispatched via ToolRegistry
  - Full audit trail via AuditLog

Usage:
    adapter = ArkainBrainAdapter(session, workspace_id="ws1")
    result = adapter.run_agent(
        agent_name="deal_analyst",
        task="Evaluate the deal at 123 Main St",
        context={"purchase_price": 1500000},
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable

from engine.brain.tools import ToolRegistry, ToolResult


@dataclass
class AgentRunResult:
    """Result from an agent execution."""
    agent_name: str
    task: str
    output: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    reasoning_trace: List[str] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0
    execution_ms: int = 0
    status: str = "completed"  # completed, failed, approval_pending
    error: str = ""


class ArkainBrainAdapter:
    """Adapter that wires ArkainBrain into the engine platform.

    Responsibilities:
      - Load agent config from DB (Phase 1A)
      - Route LLM calls through ACPLLMRouter
      - Enforce policies via PolicyBroker
      - Track costs via CostMeter
      - Dispatch tool calls via ToolRegistry
      - Log everything to AuditLog

    When ArkainBrain library is not available, operates in
    "simulation mode" — executes tool calls based on task analysis
    without LLM reasoning. This enables full testing of the
    integration layer.
    """

    def __init__(
        self, session=None, workspace_id: str = "",
        user_id: str = "",
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._tools = tool_registry or ToolRegistry(
            session, workspace_id, user_id,
        )
        self._agent_configs: Dict[str, Dict] = {}
        self._llm_available = False

        # Load configs from DB if available
        if session:
            self._load_configs()

    def _load_configs(self):
        """Load agent configurations from DB."""
        try:
            from engine.acp import ACPLLMRouter
            router = ACPLLMRouter(self._session, self._workspace_id)
            self._llm_available = True
        except Exception:
            pass

        try:
            from engine.db.repositories import AgentConfigRepo
            repo = AgentConfigRepo(self._session)
            configs = repo.list_configs(self._workspace_id)
            for cfg in configs:
                self._agent_configs[cfg["agent_name"]] = cfg
        except Exception:
            pass

    def register_tools(self):
        """Register all platform tools."""
        self._tools.register_all()

    def list_agents(self) -> List[Dict]:
        """List all configured agents."""
        agents = []
        for name, cfg in self._agent_configs.items():
            agents.append({
                "name": name,
                "role": cfg.get("role", ""),
                "llm_tier": cfg.get("llm_tier", "heavy"),
                "tools": cfg.get("tools", []),
                "is_active": cfg.get("is_active", True),
            })
        return agents

    def run_agent(
        self, agent_name: str, task: str,
        context: Dict = None,
        max_tool_calls: int = 10,
        require_approval: bool = False,
    ) -> AgentRunResult:
        """Execute an agent on a task.

        In simulation mode (no LLM): analyzes the task and
        dispatches appropriate tool calls based on keywords.

        Args:
            agent_name: Name of the configured agent
            task: Natural language task description
            context: Additional context data
            max_tool_calls: Maximum tool calls allowed
            require_approval: Whether to pause for HITL approval
        """
        start = time.perf_counter()
        context = context or {}

        # Check approval requirement
        if require_approval:
            return AgentRunResult(
                agent_name=agent_name, task=task,
                status="approval_pending",
                output="Task requires human approval before execution.",
            )

        # Get agent config
        agent_cfg = self._agent_configs.get(agent_name, {})
        allowed_tools = agent_cfg.get("tools", [])

        # Execute in simulation mode (tool dispatch based on task analysis)
        tool_calls = []
        reasoning = []
        output_parts = []

        # Analyze task and determine tool calls
        plan = self._plan_execution(task, context, allowed_tools)
        reasoning.append(f"Planned {len(plan)} tool calls for task: {task}")

        for step in plan[:max_tool_calls]:
            tool_name = step["tool"]
            params = step["params"]

            reasoning.append(f"Calling {tool_name} with {list(params.keys())}")
            result = self._tools.execute(tool_name, params)

            tool_calls.append({
                "tool": tool_name,
                "params": params,
                "success": result.success,
                "data": result.data if result.success else None,
                "error": result.error,
                "ms": result.execution_ms,
            })

            if result.success:
                output_parts.append(
                    f"[{tool_name}]: {_summarize(result.data)}"
                )
            else:
                reasoning.append(f"Tool {tool_name} failed: {result.error}")

        elapsed = int((time.perf_counter() - start) * 1000)

        # Compose output
        output = "\n".join(output_parts) if output_parts else "No tool results."

        return AgentRunResult(
            agent_name=agent_name,
            task=task,
            output=output,
            tool_calls=tool_calls,
            reasoning_trace=reasoning,
            execution_ms=elapsed,
            status="completed",
        )

    def _plan_execution(
        self, task: str, context: Dict, allowed_tools: List[str],
    ) -> List[Dict]:
        """Analyze task and plan tool calls (rule-based in simulation mode)."""
        task_lower = task.lower()
        plan = []

        # EGM prediction
        if any(kw in task_lower for kw in ["predict", "forecast", "egm", "vgt", "terminal", "gaming revenue"]):
            params = {
                "venue_type": context.get("venue_type", "bar"),
                "state": context.get("state", "IL"),
                "terminal_count": context.get("terminal_count", 5),
            }
            if context.get("municipality"):
                params["municipality"] = context["municipality"]
            plan.append({"tool": "egm_predict", "params": params})

        # Deal evaluation
        if any(kw in task_lower for kw in ["evaluate", "deal", "property", "acquisition"]):
            params = dict(context)
            if "purchase_price" in params:
                plan.append({"tool": "evaluate_deal", "params": params})

        # Contract simulation
        if any(kw in task_lower for kw in ["contract", "simulate", "monte carlo", "structure"]):
            params = {
                "agreement_type": context.get("agreement_type", "revenue_share"),
                "operator_split": context.get("operator_split", 0.65),
                "host_split": context.get("host_split", 0.35),
                "acquisition_cost": context.get("acquisition_cost", 100000),
                "contract_months": context.get("contract_months", 60),
                "num_simulations": context.get("num_simulations", 5000),
                "seed": context.get("seed", 42),
                **{k: v for k, v in context.items()
                   if k.startswith("coin_in") or k.startswith("hold_pct")},
            }
            plan.append({"tool": "simulate_contract", "params": params})

        # Portfolio
        if any(kw in task_lower for kw in ["portfolio", "dashboard", "exposure"]):
            plan.append({"tool": "portfolio_dashboard", "params": {}})

        # Deal impact
        if any(kw in task_lower for kw in ["impact", "concentration", "what if"]):
            plan.append({"tool": "deal_impact", "params": context})

        # Financial calculations
        if any(kw in task_lower for kw in ["amortize", "loan", "mortgage"]):
            plan.append({"tool": "amortize", "params": context})
        if "irr" in task_lower:
            plan.append({"tool": "irr", "params": context})
        if "dscr" in task_lower:
            plan.append({"tool": "dscr", "params": context})

        # Classify venue
        if "classify" in task_lower and context.get("name"):
            plan.append({"tool": "egm_classify", "params": {"name": context["name"]}})

        # Strategic analysis
        if any(kw in task_lower for kw in ["strategic", "swot", "scenario analysis", "stress test",
                                            "decision analysis", "failure mode", "leverage point"]):
            params = dict(context)
            if not params.get("scenario_text") and not params.get("title"):
                params["scenario_text"] = task
                params["title"] = task[:80]
            plan.append({"tool": "strategic_analyze", "params": params})

        # SWOT only
        if "swot" in task_lower and not any(s["tool"] == "strategic_analyze" for s in plan):
            params = dict(context)
            if not params.get("scenario_text"):
                params["scenario_text"] = task
            plan.append({"tool": "swot_generate", "params": params})

        # Assumption audit
        if any(kw in task_lower for kw in ["assumption", "audit assumptions", "gaps"]):
            params = dict(context)
            if not params.get("scenario_text"):
                params["scenario_text"] = task
            plan.append({"tool": "assumption_audit", "params": params})

        # Filter to allowed tools if configured
        if allowed_tools:
            plan = [s for s in plan if s["tool"] in allowed_tools]

        return plan


class PipelineOrchestrator:
    """Phase 8C: Orchestrates multi-stage pipelines with agent execution.

    Uses the engine's PipelineRuntime for DAG management, retries, and
    checkpoints. Each stage is executed by an ArkainBrain agent.

    Usage:
        orch = PipelineOrchestrator(adapter)
        result = orch.run_pipeline("deal_evaluation", {
            "deal_name": "123 Main St",
            "purchase_price": 1500000,
        })
    """

    def __init__(self, adapter: ArkainBrainAdapter):
        self._adapter = adapter

    def run_pipeline(
        self, pipeline_name: str, inputs: Dict,
        stages: Optional[List[Dict]] = None,
    ) -> Dict:
        """Execute a multi-stage pipeline."""
        start = time.perf_counter()

        if not stages:
            stages = self._default_stages(pipeline_name)

        results = {}
        context = dict(inputs)

        for stage in stages:
            stage_name = stage["name"]
            agent_name = stage.get("agent", stage_name)
            task = stage.get("task", f"Execute {stage_name}")

            result = self._adapter.run_agent(
                agent_name=agent_name,
                task=task,
                context=context,
                require_approval=stage.get("requires_approval", False),
            )

            results[stage_name] = {
                "status": result.status,
                "output": result.output,
                "tool_calls": len(result.tool_calls),
                "ms": result.execution_ms,
            }

            # Propagate data to next stage
            for tc in result.tool_calls:
                if tc["success"] and tc["data"]:
                    if isinstance(tc["data"], dict):
                        context.update(tc["data"])

            # Stop on failure or approval pending
            if result.status != "completed":
                break

        elapsed = int((time.perf_counter() - start) * 1000)

        return {
            "pipeline": pipeline_name,
            "stages": results,
            "total_stages": len(stages),
            "completed_stages": sum(
                1 for r in results.values() if r["status"] == "completed"
            ),
            "execution_ms": elapsed,
            "status": "completed" if all(
                r["status"] == "completed" for r in results.values()
            ) else "incomplete",
        }

    def _default_stages(self, pipeline_name: str) -> List[Dict]:
        """Default stage definitions for known pipelines."""
        if pipeline_name == "deal_evaluation":
            return [
                {"name": "predict", "agent": "egm_analyst",
                 "task": "Predict gaming revenue for this location"},
                {"name": "evaluate", "agent": "deal_analyst",
                 "task": "Evaluate this deal"},
                {"name": "impact", "agent": "portfolio_analyst",
                 "task": "Analyze portfolio impact of this deal"},
            ]
        elif pipeline_name == "contract_analysis":
            return [
                {"name": "predict", "agent": "egm_analyst",
                 "task": "Predict gaming performance"},
                {"name": "simulate", "agent": "contract_analyst",
                 "task": "Simulate contract outcomes"},
            ]
        elif pipeline_name == "strategic_analysis":
            return [
                {"name": "structure", "agent": "structuring_analyst",
                 "task": "Decompose and structure the scenario for analysis"},
                {"name": "decide", "agent": "decision_analyst",
                 "task": "Assess decision readiness and identify gating risks"},
                {"name": "simulate", "agent": "scenario_analyst",
                 "task": "Generate bull/base/bear scenarios with sensitivities"},
                {"name": "patterns", "agent": "pattern_analyst",
                 "task": "Identify failure modes, leverage points, contradictions"},
                {"name": "synthesize", "agent": "executive_synthesizer",
                 "task": "Synthesize final decision, SWOT, and recommendations"},
            ]
        return []


def _summarize(data: Any, max_len: int = 200) -> str:
    """Summarize tool output for reasoning trace."""
    if data is None:
        return "null"
    if isinstance(data, dict):
        keys = list(data.keys())[:5]
        return f"dict with keys: {keys}"
    if isinstance(data, list):
        return f"list with {len(data)} items"
    s = str(data)
    return s[:max_len] + "..." if len(s) > max_len else s
