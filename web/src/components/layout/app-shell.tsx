"use client";

import { useState, useCallback } from "react";
import {
  PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
  Plus, Settings, Brain, Loader2,
} from "lucide-react";
import type { Message, Artifact, Thread, DealIntakeForm } from "@/lib/contracts";
import { PROPERTY_TYPES } from "@/lib/constants";
import { buildStreamScenario } from "@/lib/streaming";
import { useStream } from "@/hooks/use-stream";
import { LeftPanel } from "./left-panel";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ArtifactPanel } from "@/components/artifacts/artifact-panel";
import { AdminPanel } from "@/components/admin/admin-panel";
import { IntakeModal } from "@/components/intake/intake-modal";
import { StrategicIntake, type StrategicInput } from "@/components/strategic/strategic-intake";
import { StrategicResultPanel, type StrategicResult } from "@/components/strategic/strategic-result";

const INITIAL_THREADS: Thread[] = [
  { id: "t1", title: "Springfield Strip Center Analysis", lastMessage: "The deal scores GO with IRR of 15.2%...", updatedAt: "2m ago", unread: true },
  { id: "t2", title: "BP Gas Station #12 Contract", lastMessage: "Monte Carlo simulation complete...", updatedAt: "1h ago", unread: false },
  { id: "t3", title: "Portfolio Concentration Review", lastMessage: "Illinois exposure is at 72%...", updatedAt: "3h ago", unread: false },
];

const INITIAL_ARTIFACTS: Artifact[] = [
  {
    id: "a1", title: "Deal Memo — 123 Main St", type: "markdown", pinned: true, threadId: "t1",
    content: "# Deal Evaluation: 123 Main St Strip Center\n\n**Decision: GO**\n\n## Metrics\n- Purchase Price: $1,500,000\n- NOI: $120,000\n- Cap Rate: 8.0%\n- DSCR: 1.25\n- IRR: 15.2%\n\n## Stage Results\n| Stage | Score | Status |\n|-------|-------|--------|\n| Intake | — | ✅ Pass |\n| Feasibility | 1.00 | ✅ Pass |\n| Market | 0.70 | ✅ Pass |\n| Cost | 1.00 | ✅ Pass |\n| Finance | 0.85 | ✅ Pass |\n| Risk | 0.75 | ✅ Pass |\n| Decision | 0.83 | **GO** |\n\n## Recommendation\nProceed with acquisition.",
  },
  {
    id: "a2", title: "Monte Carlo — BP #12", type: "json", pinned: false, threadId: "t2",
    content: JSON.stringify({ irr_p10: 0.956, irr_p50: 1.60, irr_p90: 2.575, net_win_p50: 20688, operator_cf_p50: 13447, breakeven_net_win: 2564 }, null, 2),
  },
  {
    id: "a3", title: "Portfolio Dashboard", type: "json", pinned: true, threadId: "t3",
    content: JSON.stringify({ total_assets: 4, total_value: 3770000, total_debt: 870000, leverage_ratio: 0.2308, state_hhi: 0.6847 }, null, 2),
  },
];

export function AppShell() {
  // ── UI state ───────────────────────────────────────────
  const [dark, setDark] = useState(true);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [section, setSection] = useState<"chat" | "admin" | "strategic">("chat");
  const [adminTab, setAdminTab] = useState<"models" | "policies" | "audit">("models");
  const [activeThread, setActiveThread] = useState("t1");
  const [showIntake, setShowIntake] = useState(false);
  const [showStrategicIntake, setShowStrategicIntake] = useState(false);
  const [strategicResult, setStrategicResult] = useState<StrategicResult | null>(null);
  const [strategicLoading, setStrategicLoading] = useState(false);

  // ── Data state ─────────────────────────────────────────
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: "Welcome to Arkain. I can evaluate deals, simulate contracts, forecast gaming revenue, and analyze portfolio impact.\n\nUse the **New Deal** button to submit structured property data, or describe what you need in chat.",
    },
  ]);
  const [artifacts, setArtifacts] = useState<Artifact[]>(INITIAL_ARTIFACTS);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);

  // ── Streaming ──────────────────────────────────────────
  const { streaming, streamText, streamTools, runStream } = useStream(setMessages, {
    onArtifactCreated: (artifact) => {
      setArtifacts((prev) => [artifact, ...prev]);
      setSelectedArtifact(artifact);
      if (!rightOpen) setRightOpen(true);
    },
  });

  // ── Handlers ───────────────────────────────────────────
  const handleChatSend = useCallback(
    (text: string) => {
      const simple = [
        { type: "message_delta" as const, content: "I'd be happy to help with that. " },
        { type: "message_delta" as const, content: "For the most thorough analysis, use the **New Deal** button " },
        { type: "message_delta" as const, content: "to submit structured property data — it runs the full 7-stage pipeline " },
        { type: "message_delta" as const, content: "with gaming forecasts, contract simulation, and portfolio impact.\n\n" },
        { type: "message_delta" as const, content: "Or ask me anything about your existing deals and portfolio." },
        { type: "final_message" as const },
      ];
      runStream(simple, text);
    },
    [runStream],
  );

  const handleIntakeSubmit = useCallback(
    (form: DealIntakeForm) => {
      setShowIntake(false);
      setSection("chat");
      const pt = PROPERTY_TYPES[form.property_type];
      const summary = `Evaluate ${pt?.label}: ${form.address || "property"}, ${form.state}. Price: $${Number(form.purchase_price || 0).toLocaleString()}, NOI: $${Number(form.noi || 0).toLocaleString()}.${form.include_gaming ? ` Gaming: ${form.terminal_count || 5} VGTs.` : ""}`;
      const scenario = buildStreamScenario(form);
      runStream(scenario, summary);
    },
    [runStream],
  );

  const handleTogglePin = useCallback((id: string) => {
    setArtifacts((prev) => prev.map((a) => (a.id === id ? { ...a, pinned: !a.pinned } : a)));
    setSelectedArtifact((prev) => (prev?.id === id ? { ...prev, pinned: !prev.pinned } : prev));
  }, []);

  // ── Strategic handler ──────────────────────────────────
  const handleStrategicSubmit = useCallback(async (input: StrategicInput) => {
    setShowStrategicIntake(false);
    setStrategicLoading(true);
    setSection("strategic");
    try {
      const BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/mock";
      const res = await fetch(`${BASE}/strategic?sub=analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...input,
          budget_usd: input.budget_usd ? Number(input.budget_usd) : 0,
        }),
      });
      if (!res.ok) throw new Error(`API ${res.status}`);
      const data = await res.json();
      setStrategicResult(data as StrategicResult);
    } catch (e) {
      console.error("Strategic analysis failed:", e);
    } finally {
      setStrategicLoading(false);
    }
  }, []);

  // ── Theme ──────────────────────────────────────────────
  const themeClass = dark ? "dark" : "";

  const adminLabels: Record<string, string> = {
    models: "Model Routes",
    policies: "Tool Policies",
    audit: "Audit Log",
  };

  return (
    <div className={`${themeClass} flex h-screen w-screen bg-[var(--bg)] text-[var(--text)] text-sm font-sans overflow-hidden`}>

      <IntakeModal open={showIntake} onClose={() => setShowIntake(false)} onSubmit={handleIntakeSubmit} />
      <StrategicIntake open={showStrategicIntake} onClose={() => setShowStrategicIntake(false)} onSubmit={handleStrategicSubmit} />

      {/* ── LEFT PANEL ── */}
      <div
        className="bg-[var(--surface)] border-r border-[var(--border)] flex flex-col transition-all duration-200 overflow-hidden"
        style={{ width: leftOpen ? 272 : 0, minWidth: leftOpen ? 272 : 0 }}
      >
        <LeftPanel
          dark={dark}
          onToggleTheme={() => setDark(!dark)}
          section={section}
          onSectionChange={setSection}
          threads={INITIAL_THREADS}
          activeThread={activeThread}
          onSelectThread={setActiveThread}
          adminTab={adminTab}
          onAdminTabChange={setAdminTab}
          onNewDeal={() => setShowIntake(true)}
          onNewStrategic={() => setShowStrategicIntake(true)}
        />
      </div>

      {/* ── CENTER ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Center header */}
        <div className="h-12 flex items-center justify-between px-3.5 border-b border-[var(--border)] shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setLeftOpen(!leftOpen)}
              className="p-1.5 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)]"
            >
              {leftOpen ? <PanelLeftClose size={15} /> : <PanelLeftOpen size={15} />}
            </button>
            {section === "chat" ? (
              <span className="font-semibold text-[13px]">
                {INITIAL_THREADS.find((t) => t.id === activeThread)?.title || "Chat"}
              </span>
            ) : section === "strategic" ? (
              <>
                <Brain size={14} className="text-[var(--accent)]" />
                <span className="font-semibold text-[13px]">
                  Strategic Intelligence {strategicResult ? `— ${strategicResult.title}` : ""}
                </span>
              </>
            ) : (
              <>
                <Settings size={14} className="text-[var(--accent)]" />
                <span className="font-semibold text-[13px]">Admin — {adminLabels[adminTab]}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-1">
            {section === "chat" && (
              <button
                onClick={() => setShowIntake(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                  bg-[var(--accent)]/10 text-[var(--accent)] hover:bg-[var(--accent)]/15 transition-colors"
              >
                <Plus size={13} /> New Deal
              </button>
            )}
            {section === "strategic" && (
              <button
                onClick={() => setShowStrategicIntake(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                  bg-[var(--accent)]/10 text-[var(--accent)] hover:bg-[var(--accent)]/15 transition-colors"
              >
                <Brain size={13} /> New Analysis
              </button>
            )}
            <button
              onClick={() => setRightOpen(!rightOpen)}
              className="p-1.5 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)]"
            >
              {rightOpen ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            </button>
          </div>
        </div>

        {/* Center content */}
        {section === "chat" ? (
          <ChatPanel
            messages={messages}
            streaming={streaming}
            streamText={streamText}
            streamTools={streamTools}
            onSend={handleChatSend}
          />
        ) : section === "strategic" ? (
          strategicLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-[var(--text-muted)]">
                <Loader2 size={24} className="animate-spin text-[var(--accent)]" />
                <div className="text-[13px] font-semibold">Running 5-stage pipeline…</div>
                <div className="text-[11px] text-[var(--text-dim)]">
                  Compression → Decision Prep → Scenarios → Patterns → Synthesis
                </div>
              </div>
            </div>
          ) : strategicResult ? (
            <StrategicResultPanel result={strategicResult} onExportMemo={() => {}} />
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-[var(--text-muted)]">
                <Brain size={32} className="text-[var(--accent)] opacity-40" />
                <div className="text-[13px]">No analysis yet</div>
                <button
                  onClick={() => setShowStrategicIntake(true)}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-[var(--accent)] text-white hover:opacity-90"
                >
                  <Brain size={13} /> New Strategic Analysis
                </button>
              </div>
            </div>
          )
        ) : (
          <AdminPanel activeTab={adminTab} />
        )}
      </div>

      {/* ── RIGHT PANEL ── */}
      <div
        className="bg-[var(--surface)] border-l border-[var(--border)] flex flex-col transition-all duration-200 overflow-hidden"
        style={{ width: rightOpen ? 380 : 0, minWidth: rightOpen ? 380 : 0 }}
      >
        <ArtifactPanel
          artifacts={artifacts}
          selectedArtifact={selectedArtifact}
          onSelect={setSelectedArtifact}
          onTogglePin={handleTogglePin}
        />
      </div>
    </div>
  );
}
