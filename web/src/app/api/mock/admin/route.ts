import { NextResponse } from "next/server";
import type { ModelRoute, ToolPolicy, AuditEntry } from "@/lib/contracts";

const models: ModelRoute[] = [
  { tier: "premium", model: "claude-sonnet-4-20250514", provider: "anthropic", temperature: 0.3, maxTokens: 128000, active: true },
  { tier: "heavy", model: "gpt-4.1", provider: "openai", temperature: 0.5, maxTokens: 128000, active: true },
  { tier: "light", model: "claude-haiku-4-5-20251001", provider: "anthropic", temperature: 0.7, maxTokens: 32000, active: true },
];

const policies: ToolPolicy[] = [
  { tool: "evaluate_deal", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "simulate_contract", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "egm_predict", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "portfolio_dashboard", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "deal_impact", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "amortize", allowed: true, approvalRequired: false, redactOutput: false },
  { tool: "brain_run", allowed: true, approvalRequired: true, redactOutput: false },
  { tool: "egm_ingest", allowed: true, approvalRequired: true, redactOutput: true },
];

const audit: AuditEntry[] = [
  { time: "14:22:03", event: "tool.execute", detail: "evaluate_deal → GO", user: "admin", severity: "info" },
  { time: "14:22:01", event: "tool.execute", detail: "deal_impact → CAUTION", user: "admin", severity: "warn" },
  { time: "14:18:45", event: "model.route", detail: "Resolved heavy → gpt-4.1", user: "system", severity: "info" },
  { time: "14:15:12", event: "policy.check", detail: "brain_run → approval required", user: "admin", severity: "warn" },
  { time: "13:50:00", event: "egm.ingest", detail: "IL monthly data: 1,247 locations", user: "system", severity: "info" },
  { time: "13:48:22", event: "auth.login", detail: "admin@arkain.com", user: "admin", severity: "info" },
];

export async function GET(req: Request) {
  const url = new URL(req.url);
  const type = url.searchParams.get("type") || "models";

  switch (type) {
    case "models": return NextResponse.json(models);
    case "policies": return NextResponse.json(policies);
    case "audit": return NextResponse.json(audit);
    default: return NextResponse.json({ error: "Unknown admin type" }, { status: 400 });
  }
}
