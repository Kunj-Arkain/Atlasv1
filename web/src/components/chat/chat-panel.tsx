"use client";

import { useRef, useEffect } from "react";
import { Bot, Loader2 } from "lucide-react";
import type { Message, ToolCall } from "@/lib/contracts";
import { MessageBubble } from "./message-bubble";
import { ToolCard } from "./tool-card";
import { Markdown } from "./markdown";
import { Composer } from "./composer";

interface ChatPanelProps {
  messages: Message[];
  streaming: boolean;
  streamText: string;
  streamTools: ToolCall[];
  onSend: (text: string) => void;
}

export function ChatPanel({ messages, streaming, streamText, streamTools, onSend }: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText]);

  return (
    <div className="flex flex-col flex-1 min-w-0">
      {/* Messages */}
      <div className="flex-1 overflow-auto pb-4">
        {messages.map((m, i) => (
          <MessageBubble key={i} msg={m} />
        ))}

        {/* Active stream */}
        {streaming && (
          <div className="flex gap-3 px-5 py-3.5 items-start">
            <div className="w-7 h-7 rounded-[7px] shrink-0 flex items-center justify-center bg-[var(--accent)]/10 text-[var(--accent)]">
              <Bot size={14} />
            </div>
            <div className="flex-1 min-w-0 leading-relaxed">
              {streamText && <Markdown text={streamText} />}
              {streamTools.map((tc, i) => (
                <ToolCard key={i} tool={tc} />
              ))}
              {!streamText && streamTools.length === 0 && (
                <div className="flex gap-1 py-1">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-pulse-dot"
                      style={{ animationDelay: `${i * 0.2}s` }}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      {/* Composer */}
      <Composer onSend={onSend} disabled={streaming} />
    </div>
  );
}
