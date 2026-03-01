"use client";

import {
  Zap, Sun, Moon, MessageSquare, Settings, Plus, Cpu, Shield, Activity,
  Brain,
} from "lucide-react";
import type { Thread } from "@/lib/contracts";

interface LeftPanelProps {
  dark: boolean;
  onToggleTheme: () => void;
  section: "chat" | "admin" | "strategic";
  onSectionChange: (s: "chat" | "admin" | "strategic") => void;
  threads: Thread[];
  activeThread: string;
  onSelectThread: (id: string) => void;
  adminTab: "models" | "policies" | "audit";
  onAdminTabChange: (t: "models" | "policies" | "audit") => void;
  onNewDeal: () => void;
  onNewStrategic?: () => void;
}

export function LeftPanel({
  dark, onToggleTheme, section, onSectionChange,
  threads, activeThread, onSelectThread,
  adminTab, onAdminTabChange, onNewDeal, onNewStrategic,
}: LeftPanelProps) {
  return (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div className="h-12 flex items-center justify-between px-3.5 border-b border-[var(--border)] shrink-0">
        <div className="flex items-center gap-2">
          <Zap size={17} className="text-[var(--accent)]" />
          <span className="font-bold text-[15px] tracking-tight">Arkain</span>
          <span className="text-[10px] text-[var(--text-dim)] bg-[var(--hover)] px-1.5 py-0.5 rounded">v2</span>
        </div>
        <button onClick={onToggleTheme} className="p-1.5 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)]">
          {dark ? <Sun size={14} /> : <Moon size={14} />}
        </button>
      </div>

      {/* Section tabs */}
      <div className="flex px-2.5 py-2 gap-1">
        {([
          { id: "chat" as const, icon: MessageSquare, label: "Chat" },
          { id: "strategic" as const, icon: Brain, label: "Strategy" },
          { id: "admin" as const, icon: Settings, label: "Admin" },
        ]).map((s) => (
          <button
            key={s.id}
            onClick={() => onSectionChange(s.id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[12px] rounded-md transition-colors ${
              section === s.id
                ? "bg-[var(--active)] text-[var(--accent)]"
                : "text-[var(--text-muted)] hover:bg-[var(--hover)]"
            }`}
          >
            <s.icon size={12} /> {s.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-2 py-1">
        {section === "chat" ? (
          <>
            <button
              onClick={onNewDeal}
              className="w-full flex items-center justify-center gap-1.5 py-2.5 mb-2 rounded-xl text-[13px] font-semibold
                bg-[var(--accent)]/10 border border-dashed border-[var(--accent)]/40 text-[var(--accent)] hover:bg-[var(--accent)]/15 transition-colors"
            >
              <Plus size={14} /> New Deal
            </button>
            {threads.map((thread) => (
              <button
                key={thread.id}
                onClick={() => onSelectThread(thread.id)}
                className={`w-full flex flex-col items-start gap-0.5 px-3 py-2.5 mb-0.5 rounded-lg text-left transition-colors ${
                  activeThread === thread.id
                    ? "bg-[var(--active)] border-l-2 border-l-[var(--accent)]"
                    : "hover:bg-[var(--hover)]"
                }`}
              >
                <div className="flex w-full justify-between items-center">
                  <span className="font-semibold text-xs text-[var(--text)] truncate max-w-[180px]">
                    {thread.title}
                  </span>
                  {thread.unread && (
                    <span className="bg-[var(--accent)] text-white rounded-full px-1.5 py-px text-[10px] font-semibold">
                      1
                    </span>
                  )}
                </div>
                <span className="text-[11px] text-[var(--text-dim)] truncate max-w-[210px]">
                  {thread.lastMessage}
                </span>
              </button>
            ))}
          </>
        ) : section === "strategic" ? (
          <>
            <button
              onClick={onNewStrategic}
              className="w-full flex items-center justify-center gap-1.5 py-2.5 mb-3 rounded-xl text-[13px] font-semibold
                bg-[var(--accent)]/10 border border-dashed border-[var(--accent)]/40 text-[var(--accent)] hover:bg-[var(--accent)]/15 transition-colors"
            >
              <Brain size={14} /> New Analysis
            </button>

            {/* Pipeline stages info */}
            <div className="px-3 py-2 mb-2 text-[11px] text-[var(--text-dim)] border-b border-[var(--border-light)]">
              <div className="font-semibold text-[var(--text-muted)] mb-1.5">Pipeline Stages</div>
              {["Compression", "Decision Prep", "Scenarios", "Patterns", "Synthesis"].map((s, i) => (
                <div key={i} className="flex items-center gap-1.5 py-0.5">
                  <span className="w-4 h-4 rounded-sm bg-[var(--hover)] flex items-center justify-center text-[10px] font-bold text-[var(--accent)]">
                    {i + 1}
                  </span>
                  {s}
                </div>
              ))}
            </div>

            {/* Templates */}
            <div className="px-3 py-2 text-[11px] text-[var(--text-dim)]">
              <div className="font-semibold text-[var(--text-muted)] mb-1.5">Templates</div>
              {["Acquisition", "Market Expansion", "Partnership / JV", "Gaming", "General"].map((t, i) => (
                <div key={i} className="py-0.5">• {t}</div>
              ))}
            </div>

            {/* Agent roster */}
            <div className="px-3 py-2 text-[11px] text-[var(--text-dim)] border-t border-[var(--border-light)] mt-2">
              <div className="font-semibold text-[var(--text-muted)] mb-1.5">Agent Roster</div>
              {[
                "Structuring Analyst",
                "Decision Analyst",
                "Scenario Analyst",
                "Pattern Analyst",
                "Executive Synthesizer",
              ].map((a, i) => (
                <div key={i} className="py-0.5 flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-[var(--green)]" />
                  {a}
                </div>
              ))}
            </div>
          </>
        ) : (
          <>
            {([
              { id: "models" as const, icon: Cpu, label: "Model Routes" },
              { id: "policies" as const, icon: Shield, label: "Tool Policies" },
              { id: "audit" as const, icon: Activity, label: "Audit Log" },
            ]).map((item) => (
              <button
                key={item.id}
                onClick={() => onAdminTabChange(item.id)}
                className={`w-full flex items-center gap-2 px-3 py-2.5 mb-0.5 rounded-lg text-[13px] transition-colors ${
                  adminTab === item.id
                    ? "bg-[var(--active)] text-[var(--accent)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--hover)]"
                }`}
              >
                <item.icon size={14} /> {item.label}
              </button>
            ))}
          </>
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-2.5 border-t border-[var(--border)] flex items-center gap-2 shrink-0">
        <div className="w-7 h-7 rounded-md bg-[var(--accent)] flex items-center justify-center text-white text-xs font-bold">
          A
        </div>
        <div>
          <div className="text-xs font-semibold">Admin</div>
          <div className="text-[11px] text-[var(--text-dim)]">workspace: default</div>
        </div>
      </div>
    </div>
  );
}
