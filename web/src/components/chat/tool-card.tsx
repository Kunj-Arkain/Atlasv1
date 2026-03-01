"use client";

import { useState } from "react";
import { Wrench, ChevronRight, ChevronDown, Loader2, Check } from "lucide-react";
import type { ToolCall } from "@/lib/contracts";

export function ToolCard({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="bg-[var(--hover)] border border-[var(--border)] rounded-lg my-2 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-[var(--active)] transition-colors"
      >
        <span className="flex items-center gap-2">
          <Wrench size={13} className="text-[var(--accent)]" />
          <span className="font-semibold text-xs text-[var(--text)]">{tool.name}</span>
          {tool.loading ? (
            <Loader2 size={13} className="animate-spin text-[var(--accent)]" />
          ) : (
            <Check size={13} className="text-[var(--green)]" />
          )}
        </span>
        {open ? <ChevronDown size={13} className="text-[var(--text-muted)]" /> : <ChevronRight size={13} className="text-[var(--text-muted)]" />}
      </button>

      {open && (
        <div className="px-3 pb-2.5 text-xs">
          <div className="text-[var(--text-dim)] mb-1">Input:</div>
          <pre className="bg-[var(--bg)] p-2 rounded-md overflow-auto text-[11px] text-[var(--text-muted)]">
            {JSON.stringify(tool.input, null, 2)}
          </pre>
          {tool.output && (
            <>
              <div className="text-[var(--text-dim)] mt-2 mb-1">Output:</div>
              <pre className="bg-[var(--bg)] p-2 rounded-md overflow-auto text-[11px] text-[var(--green)]">
                {JSON.stringify(tool.output, null, 2)}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
