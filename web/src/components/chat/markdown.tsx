"use client";

import React from "react";

/** Lightweight markdown: headers, bold, code, bullets */
function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*.*?\*\*|`.*?`|⚠️|✅)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**"))
      return <strong key={i} className="font-bold text-[var(--text)]">{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`"))
      return <code key={i} className="bg-[var(--hover)] px-1.5 py-0.5 rounded text-xs font-mono">{part.slice(1, -1)}</code>;
    return <span key={i}>{part}</span>;
  });
}

export function Markdown({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => {
        if (line.startsWith("# "))
          return <div key={i} className="text-lg font-bold mt-3 mb-1">{line.slice(2)}</div>;
        if (line.startsWith("## "))
          return <div key={i} className="text-[15px] font-bold mt-2.5 mb-1 text-[var(--accent)]">{line.slice(3)}</div>;
        if (line.startsWith("- "))
          return <div key={i} className="pl-3.5 relative"><span className="absolute left-0 text-[var(--text-dim)]">•</span>{renderInline(line.slice(2))}</div>;
        if (line.startsWith("|"))
          return <div key={i} className="text-xs font-mono text-[var(--text-muted)]">{line}</div>;
        if (line.trim() === "")
          return <div key={i} className="h-1.5" />;
        return <div key={i}>{renderInline(line)}</div>;
      })}
    </>
  );
}
