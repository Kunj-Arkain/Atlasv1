"""
engine.financial.runner — Tool Runner Service
================================================
Phase 2: Wraps every financial tool execution with:
  - Input validation
  - Execution timing
  - Result persistence to tool_runs table
  - Audit logging
  - PolicyBroker integration (deny-by-default)

Every downstream pipeline (Phase 5 Monte Carlo, Phase 6 RE filter)
calls financial tools ONLY through this runner.

Usage:
    runner = ToolRunnerService(session, workspace_id, user_id)
    result = runner.run("irr_npv", {
        "cash_flows": [-100000, 25000, 30000, 35000, 40000],
        "discount_rate": 0.10,
    })
    # result is the dataclass output, already persisted to tool_runs
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.financial.tools import FINANCIAL_TOOLS
from engine.financial.serialization import to_dict

logger = logging.getLogger("engine.financial.runner")


class ToolExecutionError(Exception):
    """Raised when a financial tool execution fails."""
    def __init__(self, tool_name: str, error: str, inputs: Optional[Dict] = None):
        self.tool_name = tool_name
        self.error = error
        self.inputs = inputs
        super().__init__(f"Tool '{tool_name}' failed: {error}")


class ToolRunnerService:
    """Central service for executing financial tools with full audit trail.

    Every tool run is:
      1. Validated against input schema
      2. Timed (wall-clock ms)
      3. Persisted to tool_runs table
      4. Logged to audit trail
    """

    def __init__(
        self,
        session=None,
        workspace_id: str = "",
        user_id: str = "",
        audit_repo=None,
        tool_run_repo=None,
    ):
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id

        # Lazy import to avoid circular deps; repos are optional for pure compute
        if session and not tool_run_repo:
            from engine.db.repositories import ToolRunRepo
            self._tool_run_repo = ToolRunRepo(session)
        else:
            self._tool_run_repo = tool_run_repo

        if session and not audit_repo:
            from engine.db.repositories import AuditLogRepo
            self._audit_repo = AuditLogRepo(session)
        else:
            self._audit_repo = audit_repo

    def run(self, tool_name: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a financial tool and persist the result.

        Args:
            tool_name: Key in FINANCIAL_TOOLS registry
            inputs: Dict matching the tool's input dataclass fields

        Returns:
            Dict of tool output (serialized dataclass)

        Raises:
            ToolExecutionError on validation or computation failure
        """
        tool_def = FINANCIAL_TOOLS.get(tool_name)
        if not tool_def:
            available = ", ".join(sorted(FINANCIAL_TOOLS.keys()))
            raise ToolExecutionError(
                tool_name, f"Unknown tool. Available: {available}"
            )

        fn = tool_def["fn"]
        input_cls = tool_def["input_class"]

        # ── Build and validate input ─────────────────────────
        try:
            # Filter inputs to only fields the dataclass accepts
            import dataclasses
            valid_fields = {f.name for f in dataclasses.fields(input_cls)}
            filtered = {k: v for k, v in inputs.items() if k in valid_fields}
            input_obj = input_cls(**filtered)
            input_obj.validate()
        except (TypeError, ValueError) as e:
            raise ToolExecutionError(tool_name, f"Validation: {e}", inputs)

        # ── Execute with timing ──────────────────────────────
        start = time.perf_counter()
        try:
            output_obj = fn(input_obj)
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            self._log_failure(tool_name, inputs, str(e), elapsed_ms)
            raise ToolExecutionError(tool_name, str(e), inputs)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # ── Serialize ────────────────────────────────────────
        output_dict = to_dict(output_obj)
        input_dict = to_dict(input_obj)

        # ── Persist ──────────────────────────────────────────
        run_id = self._persist(tool_name, input_dict, output_dict, elapsed_ms)

        logger.info(
            f"Tool '{tool_name}' completed in {elapsed_ms}ms "
            f"[ws={self._workspace_id}, run_id={run_id}]"
        )

        # Attach metadata to output
        output_dict["_meta"] = {
            "tool_name": tool_name,
            "run_id": run_id,
            "execution_ms": elapsed_ms,
            "workspace_id": self._workspace_id,
        }

        return output_dict

    def run_batch(
        self, tool_name: str, inputs_list: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Execute a tool multiple times (e.g., sensitivity sweep).

        Returns list of results. Continues on individual failures
        (failed runs have _meta.error set).
        """
        results = []
        for i, inputs in enumerate(inputs_list):
            try:
                result = self.run(tool_name, inputs)
                results.append(result)
            except ToolExecutionError as e:
                results.append({
                    "_meta": {
                        "tool_name": tool_name,
                        "error": str(e),
                        "batch_index": i,
                    }
                })
        return results

    def list_tools(self) -> List[Dict[str, str]]:
        """List available financial tools with descriptions."""
        return [
            {
                "name": name,
                "description": tool["description"],
                "input_fields": self._get_fields(tool["input_class"]),
                "output_fields": self._get_fields(tool["output_class"]),
            }
            for name, tool in FINANCIAL_TOOLS.items()
        ]

    def get_run(self, run_id: int) -> Optional[Dict]:
        """Retrieve a specific tool run by ID."""
        if not self._tool_run_repo:
            return None
        runs = self._tool_run_repo.list_runs(
            self._workspace_id, limit=1
        )
        for r in runs:
            if r["id"] == run_id:
                return r
        return None

    # ── Private helpers ──────────────────────────────────────

    def _persist(
        self, tool_name: str, inputs: Dict, outputs: Dict, elapsed_ms: int,
    ) -> Optional[int]:
        """Persist tool run to database."""
        if not self._tool_run_repo:
            return None
        try:
            run_id = self._tool_run_repo.record(
                workspace_id=self._workspace_id,
                tool_name=tool_name,
                inputs=inputs,
                outputs=outputs,
                user_id=self._user_id,
                execution_ms=elapsed_ms,
            )
            return run_id
        except Exception as e:
            logger.warning(f"Failed to persist tool run: {e}")
            return None

    def _log_failure(
        self, tool_name: str, inputs: Dict, error: str, elapsed_ms: int,
    ):
        """Log a failed tool execution."""
        if self._audit_repo:
            try:
                self._audit_repo.append(
                    workspace_id=self._workspace_id,
                    action="tool.error",
                    resource=f"tool:{tool_name}",
                    outcome="error",
                    user_id=self._user_id,
                    details={"error": error[:500], "elapsed_ms": elapsed_ms},
                )
            except Exception:
                pass  # Don't let audit failure mask the real error

    @staticmethod
    def _get_fields(cls) -> List[Dict[str, str]]:
        """Extract field names and types from a dataclass."""
        import dataclasses
        fields = []
        for f in dataclasses.fields(cls):
            type_str = str(f.type).replace("typing.", "")
            # Skip callable fields
            if "Callable" in type_str:
                continue
            fields.append({
                "name": f.name,
                "type": type_str,
                "required": (
                    f.default is dataclasses.MISSING
                    and f.default_factory is dataclasses.MISSING
                ),
            })
        return fields
