"use client";

import type { ModelRoute, ToolPolicy, AuditEntry } from "@/lib/contracts";

// ═══════════════════════════════════════════════════════════════
// MODEL ROUTES
// ═══════════════════════════════════════════════════════════════

const MOCK_MODELS: ModelRoute[] = [
  { tier: "premium", model: "claude-sonnet-4-20250514", provider: "anthropic", temperature: 0.3, maxTokens: 128000, active: true },
  { tier: "heavy", model: "gpt-4.1", provider: "openai", temperature: 0.5, maxTokens: 128000, active: true },
  { tier: "light", model: "claude-haiku-4-5-20251001", provider: "anthropic", temperature: 0.7, maxTokens: 32000, active: true },
];

export function ModelRoutes() {
  return (
    <div>
      <p className="text-[var(--text-muted)] mb-4 text-[13px]">
        LLM routing tiers. Changes versioned and audited.
      </p>
      <div className="flex flex-col gap-3">
        {MOCK_MODELS.map((m, i) => (
          <div key={i} className="bg-[var(--hover)] border border-[var(--border)] rounded-xl p-4">
            <div className="flex justify-between items-center mb-3">
              <div className="flex items-center gap-2">
                <span className="bg-[var(--accent)]/15 text-[var(--accent)] px-2 py-0.5 rounded-md text-[10px] font-bold uppercase">
                  {m.tier}
                </span>
                <span className="font-semibold text-[13px]">{m.model}</span>
              </div>
              <div
                className="w-2.5 h-2.5 rounded-full"
                style={{ background: m.active ? "var(--green)" : "var(--red)" }}
              />
            </div>
            <div className="grid grid-cols-4 gap-3">
              {([
                ["Provider", m.provider],
                ["Temp", m.temperature],
                ["Max Tokens", m.maxTokens.toLocaleString()],
                ["Status", m.active ? "Active" : "Off"],
              ] as const).map(([label, val], j) => (
                <div key={j}>
                  <div className="text-[10px] text-[var(--text-dim)] mb-0.5">{label}</div>
                  <div className="text-[13px] font-medium">{val}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TOOL POLICIES
// ═══════════════════════════════════════════════════════════════

const MOCK_POLICIES: ToolPolicy[] = [
  { tool: "evaluate_deal", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "simulate_contract", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "egm_predict", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "portfolio_dashboard", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "deal_impact", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "amortize", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "brain_run", allowed: true, approvalRequired: true, redactOutput: false },
  { tool: "egm_ingest", allowed: true, approvalRequired: true, redactOutput: true },
];

export function ToolPolicies() {
  return (
    <div>
      <p className="text-[var(--text-muted)] mb-4 text-[13px]">
        Deny-by-default tool policies. All calls pass PolicyBroker.
      </p>
      <div className="bg-[var(--hover)] rounded-xl border border-[var(--border)] overflow-hidden">
        <div className="grid grid-cols-[2fr_1fr_1fr_1fr] px-4 py-2.5 border-b border-[var(--border)] text-[10px] text-[var(--text-dim)] font-bold uppercase tracking-wider">
          <span>Tool</span><span>Allowed</span><span>Approval</span><span>Redact</span>
        </div>
        {MOCK_POLICIES.map((p, i) => (
          <div
            key={i}
            className={`grid grid-cols-[2fr_1fr_1fr_1fr] px-4 py-2.5 text-[13px] ${
              i < MOCK_POLICIES.length - 1 ? "border-b border-[var(--border-light)]" : ""
            }`}
          >
            <span className="font-mono text-xs font-medium">{p.tool}</span>
            <span style={{ color: p.allowed ? "var(--green)" : "var(--red)" }}>
              {p.allowed ? "✓ Yes" : "✗ No"}
            </span>
            <span style={{ color: p.approvalRequired ? "var(--yellow)" : "var(--text-dim)" }}>
              {p.approvalRequired ? "⚡ Req" : "—"}
            </span>
            <span style={{ color: p.redactOutput ? "var(--orange)" : "var(--text-dim)" }}>
              {p.redactOutput ? "🔒" : "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// AUDIT LOG
// ═══════════════════════════════════════════════════════════════

const MOCK_AUDIT: AuditEntry[] = [
  { time: "14:22:03", event: "tool.execute", detail: "evaluate_deal → GO", user: "admin", severity: "info" },
  { time: "14:22:01", event: "tool.execute", detail: "deal_impact → CAUTION", user: "admin", severity: "warn" },
  { time: "14:18:45", event: "model.route", detail: "heavy → gpt-4.1", user: "system", severity: "info" },
  { time: "14:15:12", event: "policy.check", detail: "brain_run → approval required", user: "admin", severity: "warn" },
  { time: "13:50:00", event: "egm.ingest", detail: "IL monthly: 1,247 locations", user: "system", severity: "info" },
  { time: "13:48:22", event: "auth.login", detail: "admin@arkain.com", user: "admin", severity: "info" },
];

export function AuditLog() {
  return (
    <div>
      <p className="text-[var(--text-muted)] mb-4 text-[13px]">Recent audit events.</p>
      {MOCK_AUDIT.map((e, i) => (
        <div key={i} className="flex items-center gap-3 py-2.5 border-b border-[var(--border-light)] text-[13px]">
          <span className="font-mono text-[11px] text-[var(--text-dim)] w-16 shrink-0">{e.time}</span>
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ background: e.severity === "warn" ? "var(--yellow)" : e.severity === "error" ? "var(--red)" : "var(--green)" }}
          />
          <span className="font-mono text-[11px] text-[var(--accent)] w-24 shrink-0">{e.event}</span>
          <span className="flex-1">{e.detail}</span>
          <span className="text-[11px] text-[var(--text-dim)]">{e.user}</span>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// ADMIN PANEL WRAPPER
// ═══════════════════════════════════════════════════════════════

interface AdminPanelProps {
  activeTab: "models" | "policies" | "audit";
}

export function AdminPanel({ activeTab }: AdminPanelProps) {
  return (
    <div className="flex-1 overflow-auto p-5">
      {activeTab === "models" && <ModelRoutes />}
      {activeTab === "policies" && <ToolPolicies />}
      {activeTab === "audit" && <AuditLog />}
    </div>
  );
}
