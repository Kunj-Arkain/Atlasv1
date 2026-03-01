"use client";

import { useState, useCallback, useRef } from "react";
import type { Message, ToolCall, Artifact, StreamEvent } from "@/lib/contracts";

interface UseStreamOptions {
  onArtifactCreated?: (artifact: Artifact) => void;
  onComplete?: (message: Message) => void;
}

interface UseStreamReturn {
  streaming: boolean;
  streamText: string;
  streamTools: ToolCall[];
  runStream: (events: StreamEvent[], userMessage?: string) => Promise<void>;
}

export function useStream(
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  options?: UseStreamOptions,
): UseStreamReturn {
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [streamTools, setStreamTools] = useState<ToolCall[]>([]);
  const toolsRef = useRef<ToolCall[]>([]);

  const runStream = useCallback(
    async (events: StreamEvent[], userMessage?: string) => {
      if (userMessage) {
        setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
      }

      setStreaming(true);
      setStreamText("");
      setStreamTools([]);
      toolsRef.current = [];
      let accumulated = "";

      for (const event of events) {
        // Simulate realistic timing
        const delay =
          event.type === "message_delta"
            ? 25 + Math.random() * 40
            : event.type === "final_message"
            ? 100
            : 500;
        await new Promise((r) => setTimeout(r, delay));

        switch (event.type) {
          case "message_delta":
            accumulated += event.content || "";
            setStreamText(accumulated);
            break;

          case "tool_call":
            toolsRef.current = [
              ...toolsRef.current,
              { name: event.name!, input: event.input || {}, output: null, loading: true },
            ];
            setStreamTools([...toolsRef.current]);
            break;

          case "tool_result":
            toolsRef.current = toolsRef.current.map((t) =>
              t.name === event.name && t.loading
                ? { ...t, output: event.output || null, loading: false }
                : t,
            );
            setStreamTools([...toolsRef.current]);
            break;

          case "artifact_created":
            if (event.artifact) {
              options?.onArtifactCreated?.(event.artifact);
            }
            break;

          case "final_message": {
            const finalMsg: Message = {
              role: "assistant",
              content: accumulated,
              toolCalls: [...toolsRef.current],
            };
            setMessages((prev) => [...prev, finalMsg]);
            setStreamText("");
            setStreamTools([]);
            setStreaming(false);
            options?.onComplete?.(finalMsg);
            break;
          }
        }
      }
    },
    [setMessages, options],
  );

  return { streaming, streamText, streamTools, runStream };
}
