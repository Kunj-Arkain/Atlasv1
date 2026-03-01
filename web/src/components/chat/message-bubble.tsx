"use client";

import { Bot, User } from "lucide-react";
import type { Message } from "@/lib/contracts";
import { Markdown } from "./markdown";
import { ToolCard } from "./tool-card";

export function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";

  return (
    <div className="flex gap-3 px-5 py-3.5 items-start">
      <div
        className={`w-7 h-7 rounded-[7px] shrink-0 flex items-center justify-center ${
          isUser
            ? "bg-[var(--active)] text-[var(--text-muted)]"
            : "bg-[var(--accent)]/10 text-[var(--accent)]"
        }`}
      >
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </div>
      <div className="flex-1 min-w-0 leading-relaxed">
        <Markdown text={msg.content} />
        {msg.toolCalls?.map((tc, i) => <ToolCard key={i} tool={tc} />)}
      </div>
    </div>
  );
}
