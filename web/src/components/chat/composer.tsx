"use client";

import { useState, useRef, type KeyboardEvent } from "react";
import { Send, Loader2 } from "lucide-react";

interface ComposerProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function Composer({ onSend, disabled }: ComposerProps) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
    ref.current?.focus();
  };

  const handleKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="px-4 py-2.5 border-t border-[var(--border)]">
      <div className="flex gap-2 items-end bg-[var(--surface)] border border-[var(--border)] rounded-xl pl-3.5 pr-1 py-1">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask about deals, run analysis, check portfolio..."
          rows={1}
          className="flex-1 bg-transparent border-none py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-dim)] outline-none resize-none font-sans min-h-[20px] max-h-[120px]"
        />
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          className={`shrink-0 rounded-lg p-2 transition-all ${
            disabled || !value.trim()
              ? "bg-[var(--hover)] text-[var(--text-dim)]"
              : "bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]"
          }`}
        >
          {disabled ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </div>
      <div className="flex justify-between mt-1.5 px-1">
        <span className="text-[11px] text-[var(--text-dim)]">claude-sonnet-4 · PolicyBroker active</span>
        <span className="text-[11px] text-[var(--text-dim)]">Shift+Enter for newline</span>
      </div>
    </div>
  );
}
