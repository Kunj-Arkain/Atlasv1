import type {
  Thread, Message, Artifact, ModelRoute, ToolPolicy, AuditEntry,
} from "./contracts";

// ═══════════════════════════════════════════════════════════════
// API Client — single place that knows about network calls.
// Frontend components import from here; never call fetch directly.
// ═══════════════════════════════════════════════════════════════

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/mock";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// ── Threads ──────────────────────────────────────────────────
export const api = {
  threads: {
    list: () => request<Thread[]>("/threads"),
    create: (title: string) =>
      request<Thread>("/threads", { method: "POST", body: JSON.stringify({ title }) }),
  },

  // ── Messages / Streaming ─────────────────────────────────
  messages: {
    /** Returns a ReadableStream for SSE-style consumption */
    stream: async (threadId: string, content: string) => {
      const res = await fetch(`${BASE}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId, content }),
      });
      if (!res.ok || !res.body) throw new Error("Stream failed");
      return res.body;
    },
  },

  // ── Artifacts ────────────────────────────────────────────
  artifacts: {
    list: (threadId?: string) =>
      request<Artifact[]>(`/artifacts${threadId ? `?threadId=${threadId}` : ""}`),
    get: (id: string) => request<Artifact>(`/artifacts/${id}`),
    togglePin: (id: string) =>
      request<Artifact>(`/artifacts/${id}/pin`, { method: "POST" }),
  },

  // ── Admin ────────────────────────────────────────────────
  admin: {
    models: () => request<ModelRoute[]>("/admin/models"),
    policies: () => request<ToolPolicy[]>("/admin/policies"),
    audit: () => request<AuditEntry[]>("/admin/audit"),
  },

  // ── Strategic Intelligence ──────────────────────────────
  strategic: {
    analyze: (input: Record<string, unknown>) =>
      request<Record<string, unknown>>("/strategic/analyze", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    swot: (input: Record<string, unknown>) =>
      request<Record<string, unknown>>("/strategic/swot", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    scenarios: (input: Record<string, unknown>) =>
      request<Record<string, unknown>>("/strategic/scenarios", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    stressTest: (input: Record<string, unknown>) =>
      request<Record<string, unknown>>("/strategic/stress-test", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    memo: (runId: string) =>
      request<Record<string, unknown>>("/strategic/memo", {
        method: "POST",
        body: JSON.stringify({ run_id: runId }),
      }),
    runs: () => request<Record<string, unknown>[]>("/strategic/runs"),
    getRun: (runId: string) =>
      request<Record<string, unknown>>(`/strategic/runs/${runId}`),
    templates: () =>
      request<Record<string, unknown>[]>("/strategic/templates"),
    stageRoutes: () =>
      request<Record<string, string>>("/strategic/stage-routes"),
    research: (params: Record<string, unknown>) =>
      request<Record<string, unknown>>("/strategic/research", {
        method: "POST",
        body: JSON.stringify(params),
      }),
  },

  // Multi-provider search
  search: {
    multi: (params: Record<string, unknown>) =>
      request<Record<string, unknown>>("/search/multi", {
        method: "POST",
        body: JSON.stringify(params),
      }),
    news: (params: Record<string, unknown>) =>
      request<Record<string, unknown>>("/search/news", {
        method: "POST",
        body: JSON.stringify(params),
      }),
    local: (params: Record<string, unknown>) =>
      request<Record<string, unknown>>("/search/local", {
        method: "POST",
        body: JSON.stringify(params),
      }),
  },

  // Vector memory / market intelligence
  memory: {
    similarSites: (address: string) =>
      request<Record<string, unknown>>("/memory/similar-sites", {
        method: "POST",
        body: JSON.stringify({ address }),
      }),
    constructionComps: (projectType: string, location: string) =>
      request<Record<string, unknown>>("/memory/construction-comps", {
        method: "POST",
        body: JSON.stringify({ project_type: projectType, location }),
      }),
    trends: (metric: string, location: string, state: string) =>
      request<Record<string, unknown>>("/memory/trends", {
        method: "POST",
        body: JSON.stringify({ metric, location, state }),
      }),
  },

  // Construction pipeline
  construction: {
    estimate: (params: Record<string, unknown>) =>
      request<Record<string, unknown>>("/construction/estimate", {
        method: "POST",
        body: JSON.stringify(params),
      }),
    analyze: (params: Record<string, unknown>) =>
      request<Record<string, unknown>>("/construction/analyze", {
        method: "POST",
        body: JSON.stringify(params),
      }),
    costFactors: (state: string) =>
      request<Record<string, unknown>>(`/construction/cost-factors/${state}`),
  },

  // Agent management
  agents: {
    list: () => request<Record<string, unknown>>("/agents"),
    get: (name: string) =>
      request<Record<string, unknown>>(`/agents/${name}`),
    run: (name: string, task: string, context: Record<string, unknown> = {}) =>
      request<Record<string, unknown>>(`/agents/${name}/run`, {
        method: "POST",
        body: JSON.stringify({ task, context }),
      }),
  },

  // Platform info
  platform: {
    providers: () => request<Record<string, unknown>>("/providers"),
    extensionStages: () =>
      request<Record<string, unknown>>("/strategic/extension-stages"),
  },
};
