"use client";

import { useState } from "react";
import { FileText, Pin, PinOff, Search, ChevronLeft } from "lucide-react";
import type { Artifact } from "@/lib/contracts";
import { Markdown } from "@/components/chat/markdown";

interface ArtifactPanelProps {
  artifacts: Artifact[];
  selectedArtifact: Artifact | null;
  onSelect: (artifact: Artifact | null) => void;
  onTogglePin: (id: string) => void;
}

export function ArtifactPanel({ artifacts, selectedArtifact, onSelect, onTogglePin }: ArtifactPanelProps) {
  const [tab, setTab] = useState<"pinned" | "all">("pinned");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  const filtered = artifacts.filter((a) => {
    if (searchQuery) return a.title.toLowerCase().includes(searchQuery.toLowerCase());
    if (tab === "pinned") return a.pinned;
    return true;
  });

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="h-12 flex items-center justify-between px-3.5 border-b border-[var(--border)] shrink-0">
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-[var(--accent)]" />
          <span className="font-semibold text-[13px]">Artifacts</span>
          <span className="bg-[var(--hover)] text-[var(--text-muted)] rounded-full px-2 py-px text-[10px] font-semibold">
            {artifacts.length}
          </span>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex px-2.5 py-1.5 gap-1 border-b border-[var(--border)] shrink-0">
        {(["pinned", "all"] as const).map((t) => (
          <button
            key={t}
            onClick={() => { setTab(t); setSearchQuery(""); setSearchOpen(false); onSelect(null); }}
            className={`px-2 py-1 text-xs rounded-md transition-colors ${
              tab === t && !searchQuery
                ? "bg-[var(--active)] text-[var(--accent)]"
                : "text-[var(--text-muted)] hover:bg-[var(--hover)]"
            }`}
          >
            {t === "pinned" ? "Pinned" : "All"}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => { setSearchOpen(!searchOpen); if (searchOpen) setSearchQuery(""); }}
          className={`p-1 rounded-md text-[var(--text-muted)] hover:bg-[var(--hover)] ${searchOpen ? "text-[var(--accent)]" : ""}`}
        >
          <Search size={13} />
        </button>
      </div>

      {searchOpen && (
        <div className="px-2.5 py-1.5 shrink-0">
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search artifacts..."
            autoFocus
            className="w-full bg-[var(--hover)] border border-[var(--border)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text)] font-sans outline-none placeholder:text-[var(--text-dim)]"
          />
        </div>
      )}

      {/* Content */}
      {!selectedArtifact ? (
        <div className="flex-1 overflow-auto px-2 py-1">
          {filtered.length === 0 && (
            <div className="text-center py-10 text-[var(--text-dim)] text-[13px]">
              No artifacts{tab === "pinned" ? " pinned" : ""}
            </div>
          )}
          {filtered.map((a) => (
            <button
              key={a.id}
              onClick={() => onSelect(a)}
              className="w-full flex flex-col items-start gap-1 px-3 py-2.5 mb-0.5 rounded-lg text-left hover:bg-[var(--hover)] transition-colors"
            >
              <div className="flex w-full justify-between items-center">
                <span className="font-semibold text-xs text-[var(--text)]">{a.title}</span>
                {a.pinned && <Pin size={11} className="text-[var(--accent)]" />}
              </div>
              <span className="text-[10px] bg-[var(--active)] px-1.5 py-px rounded text-[var(--text-muted)] uppercase">
                {a.type}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="flex-1 overflow-auto flex flex-col">
          {/* Preview header */}
          <div className="flex items-center gap-2 px-2.5 py-2 border-b border-[var(--border)] shrink-0">
            <button onClick={() => onSelect(null)} className="p-1 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)]">
              <ChevronLeft size={14} />
            </button>
            <span className="font-semibold text-[13px] flex-1 truncate">{selectedArtifact.title}</span>
            <button
              onClick={() => onTogglePin(selectedArtifact.id)}
              className="p-1 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)]"
            >
              {selectedArtifact.pinned ? <PinOff size={13} /> : <Pin size={13} />}
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-auto p-3.5">
            {selectedArtifact.type === "markdown" ? (
              <div className="leading-relaxed">
                <Markdown text={selectedArtifact.content} />
              </div>
            ) : (
              <pre className="bg-[var(--bg)] p-3 rounded-lg overflow-auto text-[11px] leading-relaxed text-[var(--text-muted)] whitespace-pre-wrap">
                {selectedArtifact.content}
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
