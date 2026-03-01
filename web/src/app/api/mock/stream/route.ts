import type { StreamEvent } from "@/lib/contracts";

export const runtime = "edge";

// Default scenario when no deal form is submitted (general chat)
const DEFAULT_EVENTS: StreamEvent[] = [
  { type: "message_delta", content: "I'd be happy to help with that. " },
  { type: "message_delta", content: "For the most thorough analysis, use the **New Deal** button " },
  { type: "message_delta", content: "to submit structured property data — it runs the full 7-stage pipeline " },
  { type: "message_delta", content: "with gaming forecasts, contract simulation, and portfolio impact.\n\n" },
  { type: "message_delta", content: "Or you can ask me anything about your existing deals and portfolio." },
  { type: "final_message" },
];

export async function POST(req: Request) {
  const body = await req.json();
  const events: StreamEvent[] = body.events || DEFAULT_EVENTS;

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      for (const event of events) {
        const delay =
          event.type === "message_delta"
            ? 25 + Math.random() * 40
            : event.type === "final_message"
            ? 100
            : 500;
        await new Promise((r) => setTimeout(r, delay));
        controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
      }
      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
