// ═══════════════════════════════════════════════════════════════
// contracts.ts — API Type Contracts
// Source of truth for frontend. FastAPI mirrors these later.
// ═══════════════════════════════════════════════════════════════

// ── Auth ─────────────────────────────────────────────────────
export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "operator" | "viewer";
  workspaceId: string;
}

// ── Threads ──────────────────────────────────────────────────
export interface Thread {
  id: string;
  title: string;
  lastMessage: string;
  updatedAt: string;
  unread: boolean;
}

// ── Messages ─────────────────────────────────────────────────
export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  loading?: boolean;
}

export interface Message {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolCalls?: ToolCall[];
  createdAt?: string;
}

// ── Streaming ────────────────────────────────────────────────
export type StreamEventType =
  | "message_delta"
  | "tool_call"
  | "tool_result"
  | "run_event"
  | "artifact_created"
  | "final_message"
  | "error";

export interface StreamEvent {
  type: StreamEventType;
  content?: string;
  name?: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  artifact?: Artifact;
  error?: string;
}

// ── Artifacts ────────────────────────────────────────────────
export interface Artifact {
  id: string;
  title: string;
  type: "markdown" | "json" | "pdf" | "code";
  content: string;
  pinned?: boolean;
  threadId?: string;
  createdAt?: string;
}

// ── Runs ─────────────────────────────────────────────────────
export interface Run {
  id: string;
  threadId: string;
  status: "running" | "completed" | "failed" | "approval_required";
  toolCalls: ToolCall[];
  startedAt: string;
  completedAt?: string;
}

// ── Admin: Model Routes ──────────────────────────────────────
export interface ModelRoute {
  tier: "premium" | "heavy" | "light" | "embedding";
  model: string;
  provider: string;
  temperature: number;
  maxTokens: number;
  active: boolean;
}

// ── Admin: Tool Policies ─────────────────────────────────────
export interface ToolPolicy {
  tool: string;
  allowed: boolean;
  approvalRequired: boolean;
  redactOutput: boolean;
}

// ── Admin: Audit Log ─────────────────────────────────────────
export interface AuditEntry {
  time: string;
  event: string;
  detail: string;
  user: string;
  severity: "info" | "warn" | "error";
}

// ── Deal Intake ──────────────────────────────────────────────
export interface DealIntakeForm {
  property_type: string;
  deal_name?: string;
  address?: string;
  state: string;
  municipality?: string;
  purchase_price?: string;
  noi?: string;
  year_built?: string;
  lot_size?: string;
  financing: "cash" | "financed";
  include_gaming: boolean;
  // Gaming fields
  terminal_count?: string;
  gaming_agreement?: string;
  operator_split?: string;
  gaming_operator?: string;
  // Financing fields
  down_payment_pct?: string;
  loan_rate?: string;
  loan_term_years?: string;
  lender?: string;
  // Type-specific fields stored dynamically
  [key: string]: string | boolean | undefined;
}
