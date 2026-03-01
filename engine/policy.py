"""
engine.policy — Tool Policy Gateway & Sandbox
===============================================
AUDIT ITEM #5 (Impact: 10/10)

The audit's biggest security finding:
  FileWriterTool writes arbitrary paths from model instructions
  (real_estate_pipeline.py:186-197). No path whitelist, no scoped
  filesystem, no policy approval, no output sanitizer.

  OWASP LLM Top 10 vulnerabilities exposed:
    - LLM01: Prompt Injection → model tells tool to write /etc/crontab
    - LLM02: Insecure Output Handling → no sanitization on tool results
    - LLM05: Insecure Plugin/Tool Design → no schema, no scope, no limits
    - LLM06: Excessive Agency → tools have unlimited capability
    - LLM09: Sensitive Information Disclosure → no output redaction

This module implements:
  - ToolPolicy: declarative per-tool rules (path allowlist, rate limit,
    action scope, approval requirements, redaction patterns)
  - PolicyBroker: wraps EVERY tool invocation. DENY-BY-DEFAULT.
    Tools without a registered policy are blocked.
  - SandboxedFileWriter: drop-in replacement for FileWriterTool.
    Enforces path containment on every write.
  - OutputSanitizer: strips SSNs, credit cards, API keys, passwords
    from tool outputs before they return to the LLM.

ZERO external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from engine.observability import AuditLog


# ═══════════════════════════════════════════════════════════════
# POLICY DEFINITIONS
# ═══════════════════════════════════════════════════════════════

class ActionScope(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    DELETE = "delete"


class ApprovalRequirement(str, Enum):
    NONE = "none"           # No approval needed
    AUTO = "auto"           # Auto-approve + audit log
    HUMAN = "human"         # Requires HITL approval


@dataclass
class ToolPolicy:
    """Declarative policy for a single tool.

    Every tool MUST have a registered policy. Tools without
    policies are blocked (deny-by-default).
    """
    tool_name: str
    description: str = ""

    # What this tool is allowed to do
    allowed_scopes: List[str] = field(
        default_factory=lambda: [ActionScope.READ.value]
    )

    # Filesystem containment
    path_allowlist: List[str] = field(default_factory=list)   # Roots tool can access
    path_blocklist: List[str] = field(default_factory=lambda: [
        "/etc/*", "/var/*", "/usr/*", "/bin/*", "/sbin/*",
        "/proc/*", "/sys/*", "/dev/*", "/root/*",
        "~/*", "../*", "**/.env", "**/*.key", "**/*.pem",
        "**/secrets*", "**/credentials*", "**/password*",
    ])
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10MB

    # Network
    allowed_domains: List[str] = field(default_factory=list)
    allow_egress: bool = False

    # Approval
    approval: str = ApprovalRequirement.AUTO.value

    # Rate limits
    max_calls_per_stage: int = 50
    max_calls_per_pipeline: int = 200

    # Sensitive data redaction patterns
    redact_patterns: List[str] = field(default_factory=lambda: [
        r"\b\d{3}-\d{2}-\d{4}\b",          # SSN
        r"\b(?:\d[ -]*?){13,16}\b",         # Credit card
        r"(?i)password\s*[=:]\s*\S+",       # password=xyz
        r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[=:]\s*\S+",
    ])


# ═══════════════════════════════════════════════════════════════
# POLICY VIOLATIONS
# ═══════════════════════════════════════════════════════════════

class PolicyViolation(Exception):
    """Raised when a tool invocation violates its policy."""
    def __init__(self, tool_name: str, violation_type: str, detail: str):
        self.tool_name = tool_name
        self.violation_type = violation_type
        self.detail = detail
        super().__init__(
            f"POLICY VIOLATION [{tool_name}] {violation_type}: {detail}"
        )


# ═══════════════════════════════════════════════════════════════
# PATH CONTAINMENT
# ═══════════════════════════════════════════════════════════════

def _is_path_contained(path_str: str, allowed_roots: List[str]) -> bool:
    """Check if path resolves within allowed roots.
    Guards against: path traversal, symlink escape, null bytes.
    """
    if "\x00" in path_str:
        return False
    try:
        resolved = Path(path_str).resolve()
    except (OSError, ValueError):
        return False

    for root in allowed_roots:
        try:
            root_resolved = Path(root).resolve()
            resolved.relative_to(root_resolved)
            return True
        except ValueError:
            continue
    return False


def _matches_blocklist(path_str: str, patterns: List[str]) -> bool:
    """Check if path matches any blocklist glob pattern."""
    resolved = str(Path(path_str).resolve())
    name = Path(path_str).name
    for p in patterns:
        if fnmatch(resolved, p) or fnmatch(name, p):
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# OUTPUT SANITIZER
# ═══════════════════════════════════════════════════════════════

class OutputSanitizer:
    """Strip sensitive data from tool outputs before returning to LLM.
    Addresses OWASP LLM09: Sensitive Information Disclosure.
    """

    def __init__(self, patterns: Optional[List[str]] = None):
        raw = patterns or [
            r"\b\d{3}-\d{2}-\d{4}\b",
            r"\b(?:\d[ -]*?){13,16}\b",
            r"(?i)password\s*[=:]\s*\S+",
            r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[=:]\s*\S+",
        ]
        self._compiled = [re.compile(p) for p in raw]

    def sanitize(self, text: str) -> str:
        result = text
        for pat in self._compiled:
            result = pat.sub("[REDACTED]", result)
        return result


# ═══════════════════════════════════════════════════════════════
# POLICY BROKER — Central enforcement point
# ═══════════════════════════════════════════════════════════════

class PolicyBroker:
    """Wraps every tool invocation. DENY-BY-DEFAULT.

    For every tool call:
      1. Check tool has a registered policy        → block if missing
      2. Check rate limits (per-stage, per-pipeline) → block if exceeded
      3. Check path containment (for file ops)      → block if escape
      4. Check path blocklist                       → block if sensitive
      5. Check approval requirements                → block if rejected
      6. Execute the tool
      7. Sanitize output (strip sensitive data)
      8. Log everything to audit trail

    Agents NEVER call tools directly. This broker intercepts ALL invocations.
    """

    def __init__(self, policies: Optional[List[ToolPolicy]] = None,
                 audit: Optional[AuditLog] = None,
                 output_dir: str = "",
                 tenant_id: str = "",
                 hitl_callback: Optional[Callable] = None):
        self._policies: Dict[str, ToolPolicy] = {}
        self._call_counts: Dict[str, Dict[str, int]] = {}
        self._audit = audit or AuditLog.noop()
        self._output_dir = output_dir
        self._tenant_id = tenant_id
        self._sanitizer = OutputSanitizer()
        self._hitl_callback = hitl_callback

        if policies:
            for p in policies:
                self.register_policy(p)

    def register_policy(self, policy: ToolPolicy):
        """Register a tool policy. Must be done before tool can be used."""
        self._policies[policy.tool_name] = policy
        self._call_counts[policy.tool_name] = {"_total": 0}

    def get_policy(self, tool_name: str) -> Optional[ToolPolicy]:
        return self._policies.get(tool_name)

    def invoke(self, tool_name: str, tool_fn: Callable,
               tool_input: Dict, stage_name: str = "",
               user_id: str = "") -> str:
        """Invoke a tool through the policy broker.

        This is THE entry point. Every tool call goes through here.
        Returns: tool output string (sanitized).
        Raises: PolicyViolation on any policy breach.
        """
        policy = self._policies.get(tool_name)

        # ── 1. DENY-BY-DEFAULT ──────────────────────────
        if policy is None:
            self._audit.log(
                "tool.blocked", f"tool:{tool_name}", "denied",
                user_id=user_id,
                details={"reason": "no_policy_registered"},
            )
            raise PolicyViolation(
                tool_name, "NO_POLICY",
                "Tool has no registered policy (deny-by-default)"
            )

        # ── 2. RATE LIMITS ──────────────────────────────
        counts = self._call_counts.setdefault(tool_name, {"_total": 0})
        stage_key = f"stage:{stage_name}" if stage_name else "stage:_unknown"
        stage_count = counts.get(stage_key, 0)

        if stage_count >= policy.max_calls_per_stage:
            self._audit.log(
                "tool.blocked", f"tool:{tool_name}", "denied",
                details={"reason": "rate_limit_stage", "count": stage_count},
            )
            raise PolicyViolation(
                tool_name, "RATE_LIMIT_STAGE",
                f"Exceeded {policy.max_calls_per_stage} calls in stage '{stage_name}'"
            )

        if counts["_total"] >= policy.max_calls_per_pipeline:
            self._audit.log(
                "tool.blocked", f"tool:{tool_name}", "denied",
                details={"reason": "rate_limit_pipeline", "count": counts["_total"]},
            )
            raise PolicyViolation(
                tool_name, "RATE_LIMIT_PIPELINE",
                f"Exceeded {policy.max_calls_per_pipeline} calls in pipeline"
            )

        # ── 3 & 4. PATH CONTAINMENT + BLOCKLIST ────────
        file_path = (tool_input.get("file_path")
                     or tool_input.get("path")
                     or "")
        if file_path:
            self._enforce_path_policy(file_path, policy, tool_name)

        # ── 5. APPROVAL ─────────────────────────────────
        if policy.approval == ApprovalRequirement.HUMAN.value:
            if not self._request_approval(tool_name, tool_input, stage_name):
                self._audit.log(
                    "tool.blocked", f"tool:{tool_name}", "denied",
                    details={"reason": "human_rejected"},
                )
                return json.dumps({
                    "error": "Human reviewer rejected this tool invocation",
                    "blocked": True,
                })

        # ── Hash input for audit (never log raw values) ──
        input_hash = hashlib.sha256(
            json.dumps(tool_input, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        # ── 6. EXECUTE ──────────────────────────────────
        self._audit.log(
            "tool.invoked", f"tool:{tool_name}", "started",
            user_id=user_id,
            details={"input_hash": input_hash, "stage": stage_name},
        )

        try:
            result = tool_fn(**tool_input)
        except Exception as e:
            self._audit.log(
                "tool.error", f"tool:{tool_name}", "error",
                details={"error": str(e)[:500]},
            )
            raise

        # ── 7. SANITIZE OUTPUT ──────────────────────────
        if isinstance(result, str):
            result = self._sanitizer.sanitize(result)

        # ── 8. UPDATE COUNTS + AUDIT ────────────────────
        counts["_total"] += 1
        counts[stage_key] = stage_count + 1

        self._audit.log(
            "tool.completed", f"tool:{tool_name}", "success",
            details={
                "input_hash": input_hash,
                "output_size": len(str(result)),
            },
        )

        return result

    def _enforce_path_policy(self, file_path: str, policy: ToolPolicy,
                              tool_name: str):
        """Enforce path containment + blocklist."""
        allowed_roots = list(policy.path_allowlist)
        if self._output_dir:
            allowed_roots.append(self._output_dir)

        if not allowed_roots:
            raise PolicyViolation(
                tool_name, "PATH_NO_ROOT",
                "No allowed path roots configured"
            )

        if not _is_path_contained(file_path, allowed_roots):
            raise PolicyViolation(
                tool_name, "PATH_ESCAPE",
                f"Path '{file_path}' is outside allowed directories"
            )

        if _matches_blocklist(file_path, policy.path_blocklist):
            raise PolicyViolation(
                tool_name, "PATH_BLOCKED",
                f"Path '{file_path}' matches blocklist pattern"
            )

    def _request_approval(self, tool_name: str, tool_input: Dict,
                           stage_name: str) -> bool:
        if self._hitl_callback:
            return self._hitl_callback(tool_name, tool_input, stage_name)
        # FAIL-CLOSED: No callback → DENY (P0.4 fix)
        # Enterprise expectation: "no approver configured" blocks, not approves.
        self._audit.log(
            "tool.approval_denied", f"tool:{tool_name}", "denied",
            details={"reason": "no_hitl_callback_configured",
                     "policy": "fail_closed"},
        )
        return False

    def wrap_tool(self, tool_fn: Callable, tool_name: str,
                  stage_name: str = "") -> Callable:
        """Return a wrapped tool function that enforces policy.

        Use this to wrap tools before handing them to agents:
            safe_write = broker.wrap_tool(file_writer._run, "write_file", "research")
        """
        broker = self

        def wrapped(**kwargs) -> str:
            return broker.invoke(tool_name, tool_fn, kwargs, stage_name=stage_name)

        wrapped.__name__ = f"policy_wrapped_{tool_name}"
        return wrapped


# ═══════════════════════════════════════════════════════════════
# SANDBOXED FILE WRITER
# ═══════════════════════════════════════════════════════════════

class SandboxedFileWriter:
    """Drop-in replacement for V1's FileWriterTool.

    AUDIT FIX #4: Lock file writes to output_dir.

    Every write:
      - Resolves relative to output_dir
      - Validates path containment (no escape)
      - Rejects path traversal (../)
      - Enforces file size limit
      - Logs to audit trail
    """

    def __init__(self, output_dir: str,
                 max_file_size: int = 10 * 1024 * 1024,
                 audit: Optional[AuditLog] = None):
        self._output_dir = Path(output_dir).resolve()
        self._max_size = max_file_size
        self._audit = audit or AuditLog.noop()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def write(self, file_path: str, content: str) -> Dict[str, Any]:
        """Write content to a file. Enforces path containment.

        Args:
            file_path: relative path within output_dir (e.g. "reports/model.json")
            content: text content to write

        Returns:
            {"status": "saved", "path": "...", "size_bytes": N} on success
            {"error": "...", "blocked": True} on violation
        """
        # Resolve relative to output_dir
        target = (self._output_dir / file_path).resolve()

        # Path containment check
        try:
            target.relative_to(self._output_dir)
        except ValueError:
            self._audit.log(
                "file.write_blocked", f"file:{file_path}", "denied",
                details={"reason": "path_escape"},
            )
            return {
                "error": f"Path '{file_path}' escapes output directory",
                "blocked": True,
            }

        # Size check
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._max_size:
            return {
                "error": f"Content exceeds {self._max_size} byte limit",
                "blocked": True,
            }

        # Write
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content_bytes)
        except OSError as e:
            return {"error": str(e)}

        rel = str(target.relative_to(self._output_dir))
        self._audit.log(
            "file.written", f"file:{rel}", "success",
            details={"size_bytes": len(content_bytes)},
        )

        return {
            "status": "saved",
            "path": rel,
            "size_bytes": len(content_bytes),
        }

    def read(self, file_path: str) -> Dict[str, Any]:
        """Read a file. Enforces path containment."""
        target = (self._output_dir / file_path).resolve()
        try:
            target.relative_to(self._output_dir)
        except ValueError:
            return {"error": f"Path '{file_path}' escapes output directory", "blocked": True}

        if not target.exists():
            return {"error": f"File not found: {file_path}"}

        try:
            content = target.read_text(encoding="utf-8")
            return {"status": "read", "path": file_path, "content": content,
                    "size_bytes": len(content.encode("utf-8"))}
        except OSError as e:
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# POLICY FACTORIES — Common presets
# ═══════════════════════════════════════════════════════════════

def file_writer_policy(output_dir: str) -> ToolPolicy:
    """Locked-down policy for file-writing tools."""
    return ToolPolicy(
        tool_name="write_file",
        description="Write content to files within output directory only",
        allowed_scopes=[ActionScope.WRITE.value],
        path_allowlist=[output_dir],
        approval=ApprovalRequirement.AUTO.value,
        max_calls_per_stage=20,
        max_calls_per_pipeline=100,
    )


def read_only_policy(tool_name: str) -> ToolPolicy:
    """Policy for read-only tools (search, lookup, etc.)."""
    return ToolPolicy(
        tool_name=tool_name,
        description=f"Read-only tool: {tool_name}",
        allowed_scopes=[ActionScope.READ.value],
        approval=ApprovalRequirement.NONE.value,
        max_calls_per_stage=50,
        max_calls_per_pipeline=300,
    )


def api_tool_policy(tool_name: str, allowed_domains: Optional[List[str]] = None) -> ToolPolicy:
    """Policy for tools that make external API calls."""
    return ToolPolicy(
        tool_name=tool_name,
        description=f"API tool: {tool_name}",
        allowed_scopes=[ActionScope.READ.value, ActionScope.NETWORK.value],
        allow_egress=True,
        allowed_domains=allowed_domains or [],
        approval=ApprovalRequirement.AUTO.value,
        max_calls_per_stage=30,
        max_calls_per_pipeline=150,
    )


# ═══════════════════════════════════════════════════════════════
# DEFAULT POLICIES — Register all platform tools
# ═══════════════════════════════════════════════════════════════

def build_default_policies() -> List[ToolPolicy]:
    """Build default policies for all platform tools.

    DENY-BY-DEFAULT: Only tools listed here can be invoked.
    """
    policies = []

    # ── Financial tools (read-only computations) ──
    for name in ("amortize", "irr", "dscr", "cap_rate", "cash_on_cash"):
        policies.append(read_only_policy(name))

    # ── EGM tools (read-only predictions) ──
    for name in ("egm_predict", "egm_classify", "egm_market_health"):
        policies.append(read_only_policy(name))

    # ── Contract tools (read-only simulations) ──
    for name in ("simulate_contract", "compare_structures"):
        policies.append(read_only_policy(name))

    # ── Deal / Portfolio tools (read-only analysis) ──
    for name in ("evaluate_deal", "portfolio_dashboard", "deal_impact"):
        policies.append(read_only_policy(name))

    # ── Search tools (network access required) ──
    for name in ("web_search", "multi_search", "news_search", "local_search"):
        policies.append(api_tool_policy(name, allowed_domains=[
            "google.com", "serper.dev", "api.anthropic.com",
            "googleapis.com", "voyageai.com",
        ]))

    # ── Market research (network + high cost) ──
    policies.append(ToolPolicy(
        tool_name="market_research",
        description="Deep market research: 15-20 web searches per call",
        allowed_scopes=[ActionScope.READ.value, ActionScope.NETWORK.value],
        allow_egress=True,
        approval=ApprovalRequirement.AUTO.value,
        max_calls_per_stage=3,      # Expensive — limit
        max_calls_per_pipeline=5,
    ))

    # ── Construction tools (read-only) ──
    for name in ("construction_estimate", "construction_feasibility"):
        policies.append(read_only_policy(name))

    # ── Vector store (read-only lookups) ──
    for name in ("find_similar_sites", "find_construction_comps"):
        policies.append(read_only_policy(name))

    # ── Strategic pipeline tools ──
    for name in ("strategic_analyze", "swot_generate", "decision_stress_test",
                 "scenario_simulate", "assumption_audit"):
        policies.append(ToolPolicy(
            tool_name=name,
            description=f"Strategic analysis: {name}",
            allowed_scopes=[ActionScope.READ.value, ActionScope.NETWORK.value],
            allow_egress=True,
            approval=ApprovalRequirement.AUTO.value,
            max_calls_per_stage=5,
            max_calls_per_pipeline=20,
        ))

    # ── Domain tools (Phase 12) ──
    for name in ("pull_comps", "county_tax_lookup"):
        policies.append(api_tool_policy(name))

    policies.append(read_only_policy("analyze_lease"))
    policies.append(read_only_policy("generate_term_sheets"))
    policies.append(read_only_policy("eb5_job_impact"))

    return policies
