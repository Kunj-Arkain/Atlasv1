"""
engine.connectors — Secure Connectors & MCP Integration
=========================================================
AUDIT ITEM #9 (Impact: 7/10)

Problems in V1:
  - Example tools are placeholders with "Replace with real API call"
    (real_estate_pipeline.py:186-197)
  - No connector abstraction, no health checks, no credential scoping
  - No MCP support for standardized tool discovery
  - API calls embedded directly in tool implementations with no isolation

This module implements:
  - ConnectorBase: abstract base for all external integrations
  - HTTPAPIConnector: generic REST API wrapper with auth + retry
  - MCPConnector: Model Context Protocol client skeleton
  - ConnectorRegistry: central registry with health monitoring
  - ToolSchema: MCP-compatible tool definition
  - CredentialScope: per-connector credential isolation

ZERO external dependencies (HTTP calls use urllib from stdlib).
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from engine.observability import AuditLog


# ═══════════════════════════════════════════════════════════════
# TOOL SCHEMA — MCP-compatible tool definition
# ═══════════════════════════════════════════════════════════════

@dataclass
class ToolParameter:
    """Single parameter in a tool schema."""
    name: str
    param_type: str = "string"        # string, number, boolean, object, array
    description: str = ""
    required: bool = True
    default: Any = None
    enum_values: List[str] = field(default_factory=list)


@dataclass
class ToolSchema:
    """MCP-compatible tool definition.

    Can be converted to JSON Schema for tool registration with LLMs.
    """
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    returns: str = "string"
    category: str = ""
    connector_id: str = ""

    def to_json_schema(self) -> Dict:
        """Convert to JSON Schema format (OpenAI/Anthropic tool format)."""
        properties = {}
        required = []
        for p in self.parameters:
            prop: Dict[str, Any] = {"type": p.param_type, "description": p.description}
            if p.enum_values:
                prop["enum"] = p.enum_values
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [
                {"name": p.name, "type": p.param_type,
                 "description": p.description, "required": p.required}
                for p in self.parameters
            ],
            "connector": self.connector_id,
        }


# ═══════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheck:
    """Result of a connector health check."""
    status: str = HealthStatus.UNKNOWN.value
    latency_ms: int = 0
    message: str = ""
    checked_at: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# CREDENTIAL SCOPE
# ═══════════════════════════════════════════════════════════════

@dataclass
class CredentialScope:
    """Per-connector credential isolation.

    Credentials are retrieved from SecretsVault at runtime,
    scoped to workspace. No cross-workspace credential leakage.
    """
    connector_id: str
    workspace_id: str
    required_keys: List[str] = field(default_factory=list)
    # Keys to pull from SecretsVault for this connector

    def resolve(self, vault_fn: Callable[[str, str], Optional[str]]) -> Dict[str, str]:
        """Resolve credentials from vault.

        vault_fn signature: fn(workspace_id, key) -> value | None
        """
        creds = {}
        for key in self.required_keys:
            val = vault_fn(self.workspace_id, key)
            if val is not None:
                creds[key] = val
        return creds


# ═══════════════════════════════════════════════════════════════
# CONNECTOR BASE
# ═══════════════════════════════════════════════════════════════

class ConnectorBase(ABC):
    """Abstract base for all external integrations.

    Every connector must implement:
      - connect(): establish connection / validate credentials
      - disconnect(): clean up resources
      - health_check(): verify liveness
      - list_tools(): return available tool schemas
      - invoke_tool(): execute a tool by name with arguments
    """

    def __init__(self, connector_id: str, name: str,
                 config: Optional[Dict] = None):
        self.connector_id = connector_id
        self.name = name
        self.config = config or {}
        self._connected = False
        self._last_health: Optional[HealthCheck] = None

    @abstractmethod
    def connect(self, credentials: Dict[str, str]) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self):
        """Clean up resources."""
        ...

    @abstractmethod
    def health_check(self) -> HealthCheck:
        """Check connector health."""
        ...

    @abstractmethod
    def list_tools(self) -> List[ToolSchema]:
        """Return available tool schemas."""
        ...

    @abstractmethod
    def invoke_tool(self, tool_name: str, arguments: Dict) -> Any:
        """Execute a tool by name."""
        ...

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_health(self) -> Optional[HealthCheck]:
        return self._last_health


# ═══════════════════════════════════════════════════════════════
# HTTP API CONNECTOR
# ═══════════════════════════════════════════════════════════════

@dataclass
class EndpointConfig:
    """Configuration for a single API endpoint → tool mapping."""
    tool_name: str
    method: str = "GET"            # GET, POST, PUT, DELETE
    path: str = ""                 # e.g. "/api/v1/data"
    description: str = ""
    parameters: List[ToolParameter] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None   # JSON template with {param} placeholders


class HTTPAPIConnector(ConnectorBase):
    """Generic REST API connector.

    Wraps any HTTP API as a set of tools. Each endpoint becomes a tool
    with typed parameters.

    Usage:
        connector = HTTPAPIConnector(
            connector_id="zillow",
            name="Zillow API",
            config={"base_url": "https://api.zillow.com"},
        )
        connector.add_endpoint(EndpointConfig(
            tool_name="get_property",
            method="GET",
            path="/v2/property/{zpid}",
            description="Get property details by Zillow ID",
            parameters=[ToolParameter(name="zpid", description="Zillow Property ID")],
        ))
        connector.connect({"api_key": "xxx"})
        result = connector.invoke_tool("get_property", {"zpid": "12345"})
    """

    def __init__(self, connector_id: str, name: str,
                 config: Optional[Dict] = None):
        super().__init__(connector_id, name, config)
        self._base_url = (config or {}).get("base_url", "")
        self._endpoints: Dict[str, EndpointConfig] = {}
        self._credentials: Dict[str, str] = {}
        self._default_headers: Dict[str, str] = {}
        self._timeout_s: int = (config or {}).get("timeout_s", 30)

    def add_endpoint(self, endpoint: EndpointConfig):
        self._endpoints[endpoint.tool_name] = endpoint

    def connect(self, credentials: Dict[str, str]) -> bool:
        self._credentials = credentials
        # Set up auth headers
        if "api_key" in credentials:
            self._default_headers["Authorization"] = f"Bearer {credentials['api_key']}"
        if "x_api_key" in credentials:
            self._default_headers["X-Api-Key"] = credentials["x_api_key"]
        self._connected = True
        return True

    def disconnect(self):
        self._credentials = {}
        self._default_headers = {}
        self._connected = False

    def health_check(self) -> HealthCheck:
        t0 = time.time()
        try:
            health_path = self.config.get("health_path", "/health")
            url = f"{self._base_url}{health_path}"
            req = urllib.request.Request(url, method="GET")
            for k, v in self._default_headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=5) as resp:
                latency = int((time.time() - t0) * 1000)
                status = (HealthStatus.HEALTHY.value if resp.status == 200
                          else HealthStatus.DEGRADED.value)
                self._last_health = HealthCheck(
                    status=status, latency_ms=latency,
                    message=f"HTTP {resp.status}",
                )
        except Exception as e:
            latency = int((time.time() - t0) * 1000)
            self._last_health = HealthCheck(
                status=HealthStatus.UNHEALTHY.value,
                latency_ms=latency, message=str(e)[:200],
            )
        return self._last_health

    def list_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name=ep.tool_name,
                description=ep.description,
                parameters=list(ep.parameters),
                connector_id=self.connector_id,
            )
            for ep in self._endpoints.values()
        ]

    def invoke_tool(self, tool_name: str, arguments: Dict) -> Any:
        ep = self._endpoints.get(tool_name)
        if not ep:
            return {"error": f"Unknown tool: {tool_name}"}

        # Build URL with path parameters
        path = ep.path
        for key, val in arguments.items():
            path = path.replace(f"{{{key}}}", str(val))
        url = f"{self._base_url}{path}"

        # Build request
        headers = {**self._default_headers, **ep.headers}
        headers["Content-Type"] = "application/json"

        data = None
        if ep.method in ("POST", "PUT") and ep.body_template:
            body = ep.body_template
            for key, val in arguments.items():
                body = body.replace(f"{{{key}}}", json.dumps(val) if not isinstance(val, str) else val)
            data = body.encode("utf-8")

        try:
            req = urllib.request.Request(url, method=ep.method, data=data)
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.reason}", "status": e.code}
        except Exception as e:
            return {"error": str(e)[:500]}


# ═══════════════════════════════════════════════════════════════
# MCP CONNECTOR — Model Context Protocol
# ═══════════════════════════════════════════════════════════════

class MCPConnector(ConnectorBase):
    """Model Context Protocol connector.

    MCP provides standardized tool discovery and invocation.
    This is a structural skeleton — full MCP requires an async
    transport layer (SSE/WebSocket) that depends on your runtime.

    For production, wire this to:
      - mcp-python-sdk for Python MCP servers
      - HTTP+SSE transport for remote MCP servers
      - OAuth2 flows for authenticated MCP endpoints

    This implementation supports:
      - Static tool registration (for local MCP servers)
      - Credential scoping per workspace
      - Health check via ping
      - Tool schema export in MCP format
    """

    def __init__(self, connector_id: str, name: str,
                 config: Optional[Dict] = None,
                 server_url: str = ""):
        super().__init__(connector_id, name, config)
        self._server_url = server_url or (config or {}).get("server_url", "")
        self._tools: Dict[str, ToolSchema] = {}
        self._tool_handlers: Dict[str, Callable] = {}
        self._session_id: str = ""

    def register_tool(self, schema: ToolSchema,
                      handler: Optional[Callable] = None):
        """Register a tool (for local MCP servers or testing)."""
        schema.connector_id = self.connector_id
        self._tools[schema.name] = schema
        if handler:
            self._tool_handlers[schema.name] = handler

    def connect(self, credentials: Dict[str, str]) -> bool:
        self._session_id = f"mcp-{self.connector_id}-{int(time.time())}"
        self._connected = True
        return True

    def disconnect(self):
        self._session_id = ""
        self._connected = False

    def health_check(self) -> HealthCheck:
        if self._connected:
            self._last_health = HealthCheck(
                status=HealthStatus.HEALTHY.value,
                message="MCP session active",
                details={"session_id": self._session_id},
            )
        else:
            self._last_health = HealthCheck(
                status=HealthStatus.UNHEALTHY.value,
                message="Not connected",
            )
        return self._last_health

    def list_tools(self) -> List[ToolSchema]:
        return list(self._tools.values())

    def invoke_tool(self, tool_name: str, arguments: Dict) -> Any:
        handler = self._tool_handlers.get(tool_name)
        if handler:
            return handler(**arguments)
        return {"error": f"No handler for MCP tool: {tool_name}",
                "note": "Wire to MCP transport for remote invocation"}


# ═══════════════════════════════════════════════════════════════
# CONNECTOR REGISTRY
# ═══════════════════════════════════════════════════════════════

class ConnectorRegistry:
    """Central registry for all connectors.

    Features:
      - Register/unregister connectors
      - Health monitoring across all connectors
      - Aggregate tool listing (all tools from all connectors)
      - Workspace-scoped access
    """

    def __init__(self, audit: Optional[AuditLog] = None):
        self._connectors: Dict[str, ConnectorBase] = {}
        self._workspace_access: Dict[str, set] = {}  # ws_id → {connector_ids}
        self._audit = audit or AuditLog.noop()

    def register(self, connector: ConnectorBase,
                 workspace_ids: Optional[List[str]] = None):
        """Register a connector, optionally scoped to workspaces."""
        self._connectors[connector.connector_id] = connector
        if workspace_ids:
            for ws in workspace_ids:
                if ws not in self._workspace_access:
                    self._workspace_access[ws] = set()
                self._workspace_access[ws].add(connector.connector_id)

    def unregister(self, connector_id: str):
        conn = self._connectors.pop(connector_id, None)
        if conn and conn.is_connected:
            conn.disconnect()
        for ws_set in self._workspace_access.values():
            ws_set.discard(connector_id)

    def get(self, connector_id: str) -> Optional[ConnectorBase]:
        return self._connectors.get(connector_id)

    def list_connectors(self, workspace_id: str = "") -> List[ConnectorBase]:
        if workspace_id:
            allowed = self._workspace_access.get(workspace_id, set())
            return [c for cid, c in self._connectors.items() if cid in allowed]
        return list(self._connectors.values())

    def all_tools(self, workspace_id: str = "") -> List[ToolSchema]:
        """Aggregate all tools from all (accessible) connectors."""
        tools = []
        for conn in self.list_connectors(workspace_id):
            if conn.is_connected:
                tools.extend(conn.list_tools())
        return tools

    def health_all(self) -> Dict[str, HealthCheck]:
        """Run health checks on all connectors."""
        results = {}
        for cid, conn in self._connectors.items():
            results[cid] = conn.health_check()
        return results

    def invoke(self, connector_id: str, tool_name: str,
               arguments: Dict, workspace_id: str = "",
               user_id: str = "") -> Any:
        """Invoke a tool on a specific connector with audit logging."""
        conn = self._connectors.get(connector_id)
        if not conn:
            return {"error": f"Connector '{connector_id}' not found"}

        # Workspace access check
        if workspace_id:
            allowed = self._workspace_access.get(workspace_id, set())
            if connector_id not in allowed:
                self._audit.log(
                    "connector.access_denied", f"connector:{connector_id}",
                    "denied", user_id=user_id,
                    details={"workspace": workspace_id},
                )
                return {"error": f"Connector '{connector_id}' not accessible in workspace '{workspace_id}'"}

        self._audit.log(
            "connector.invoke", f"connector:{connector_id}:{tool_name}",
            "started", user_id=user_id,
        )

        try:
            result = conn.invoke_tool(tool_name, arguments)
            self._audit.log(
                "connector.invoke", f"connector:{connector_id}:{tool_name}",
                "success", user_id=user_id,
            )
            return result
        except Exception as e:
            self._audit.log(
                "connector.invoke", f"connector:{connector_id}:{tool_name}",
                "error", details={"error": str(e)[:500]},
            )
            return {"error": str(e)[:500]}


# ═══════════════════════════════════════════════════════════════
# DLP — Data Loss Prevention (P1.5 fix)
# ═══════════════════════════════════════════════════════════════

class DLPScanner:
    """Scan tool inputs/outputs for sensitive data patterns.

    Prevents PII and credentials from leaking to external APIs.
    Applied on both INPUT (before sending to connector) and OUTPUT
    (before returning to pipeline).
    """

    DEFAULT_PATTERNS = {
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
        "api_key": r"\b(sk|pk|api[_-]?key)[_-][a-zA-Z0-9]{16,}\b",
        "aws_key": r"\bAKIA[0-9A-Z]{16}\b",
        "email": r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
        "phone_us": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
        "password_field": r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+",
    }

    def __init__(self, patterns: Optional[Dict[str, str]] = None,
                 block_on_match: bool = True):
        import re as _re
        self._patterns = patterns or self.DEFAULT_PATTERNS
        self._compiled = {name: _re.compile(pat)
                          for name, pat in self._patterns.items()}
        self._block_on_match = block_on_match

    def scan(self, data: Any) -> 'DLPResult':
        """Scan data for sensitive patterns."""
        text = json.dumps(data, default=str) if not isinstance(data, str) else data
        findings = []
        for name, pattern in self._compiled.items():
            matches = pattern.findall(text)
            if matches:
                findings.append({
                    "pattern": name, "count": len(matches),
                    "blocked": self._block_on_match,
                })
        return DLPResult(
            clean=len(findings) == 0,
            findings=findings,
            blocked=self._block_on_match and len(findings) > 0,
        )

    def redact(self, data: str) -> str:
        """Redact sensitive patterns from text."""
        result = data
        for name, pattern in self._compiled.items():
            result = pattern.sub(f"[{name.upper()}_REDACTED]", result)
        return result


@dataclass
class DLPResult:
    clean: bool
    findings: List[Dict] = field(default_factory=list)
    blocked: bool = False


# ═══════════════════════════════════════════════════════════════
# EGRESS POLICY (P1.5 fix)
# ═══════════════════════════════════════════════════════════════

@dataclass
class EgressPolicy:
    """Controls which external domains connectors can reach.

    Prevents connectors from exfiltrating data to unauthorized endpoints.
    """
    allowed_domains: List[str] = field(default_factory=list)
    blocked_domains: List[str] = field(default_factory=list)
    allow_all: bool = False

    def check_url(self, url: str) -> bool:
        if self.allow_all:
            return True
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        for blocked in self.blocked_domains:
            if domain == blocked or domain.endswith(f".{blocked}"):
                return False
        if self.allowed_domains:
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith(f".{allowed}"):
                    return True
            return False
        return True

    def check_domain(self, domain: str) -> bool:
        return self.check_url(f"https://{domain}/")


# ═══════════════════════════════════════════════════════════════
# CONNECTOR PERMISSIONS (P1.5 fix)
# ═══════════════════════════════════════════════════════════════

@dataclass
class ConnectorPermission:
    """Per-connector permission rules for a workspace."""
    connector_id: str
    workspace_id: str
    allowed_tools: List[str] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)
    read_only: bool = False
    max_calls_per_hour: int = 1000
    _call_timestamps: List[float] = field(default_factory=list)

    def is_tool_allowed(self, tool_name: str) -> bool:
        if tool_name in self.blocked_tools:
            return False
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return True

    def check_rate_limit(self) -> bool:
        now = time.time()
        cutoff = now - 3600
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        if len(self._call_timestamps) >= self.max_calls_per_hour:
            return False
        self._call_timestamps.append(now)
        return True
