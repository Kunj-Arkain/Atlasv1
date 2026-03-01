import type { StreamEvent, DealIntakeForm } from "./contracts";
import { PROPERTY_TYPES } from "./constants";

// ═══════════════════════════════════════════════════════════════
// Stream Parser — reads NDJSON events from fetch body stream.
// Both mock and FastAPI emit the same event format.
// ═══════════════════════════════════════════════════════════════

export async function* parseStream(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(":")) continue; // skip heartbeats
        try {
          yield JSON.parse(trimmed) as StreamEvent;
        } catch {
          // skip malformed lines
        }
      }
    }
    // flush
    if (buffer.trim()) {
      try {
        yield JSON.parse(buffer.trim()) as StreamEvent;
      } catch {
        // skip
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ═══════════════════════════════════════════════════════════════
// Stream Scenario Builder — generates mock stream events from
// intake form data. Used by mock API route.
// ═══════════════════════════════════════════════════════════════

export function buildStreamScenario(form: DealIntakeForm): StreamEvent[] {
  const pt = PROPERTY_TYPES[form.property_type];
  const hasGaming = form.include_gaming && pt?.gamingEligible;
  const addr = form.address || "the subject property";
  const price = Number(form.purchase_price || 0);
  const noi = Number(form.noi || 0);
  const cap = noi && price ? ((noi / price) * 100).toFixed(1) : "7.5";
  const irr = (12 + Math.random() * 8).toFixed(1);
  const dscr = (1.1 + Math.random() * 0.4).toFixed(2);
  const decision = Number(cap) >= 6 ? "GO" : "HOLD";

  const events: StreamEvent[] = [
    { type: "message_delta", content: `I'll run the full **7-stage evaluation pipeline** ` },
    { type: "message_delta", content: `for **${addr}** ` },
    { type: "message_delta", content: `(${pt?.label || form.property_type}).\n\n` },
  ];

  if (hasGaming) {
    events.push(
      { type: "message_delta", content: `This property is gaming-eligible, so I'll include a **VGT revenue forecast** ` },
      { type: "message_delta", content: `for ${form.terminal_count || "5"} terminals.\n\n` },
      { type: "tool_call", name: "egm_predict", input: { venue_type: form.property_type, state: form.state, terminal_count: Number(form.terminal_count || 5) } },
      { type: "tool_result", name: "egm_predict", output: { net_win: { p10: 11200, p50: 20800, p90: 34500 }, coin_in: { p50: 80000 }, hold_pct: { p50: 0.26 } } },
      { type: "message_delta", content: `Gaming forecast: **$20,800/mo** median net win (p10: $11.2K, p90: $34.5K).\n\n` },
    );
  }

  const evalTool = hasGaming ? "evaluate_with_gaming" : "evaluate_deal";
  events.push(
    { type: "tool_call", name: evalTool, input: { purchase_price: price, noi, property_type: form.property_type, address: addr, state: form.state } },
    { type: "tool_result", name: evalTool, output: { decision, total_score: decision === "GO" ? 0.83 : 0.52, irr: Number(irr) / 100, dscr: Number(dscr), cap_rate: Number(cap) / 100 } },
    { type: "message_delta", content: `**Decision: ${decision}** ${decision === "GO" ? "✅" : "⚠️"}\n\n` },
    { type: "message_delta", content: `Key metrics:\n` },
    { type: "message_delta", content: `- **Cap Rate:** ${cap}%\n` },
    { type: "message_delta", content: `- **DSCR:** ${dscr}\n` },
    { type: "message_delta", content: `- **Projected IRR:** ${irr}%\n` },
    { type: "message_delta", content: hasGaming ? `- **Gaming Net Win (p50):** $20,800/mo\n\n` : `\n` },
    { type: "tool_call", name: "deal_impact", input: { name: addr, state: form.state, current_value: price } },
    { type: "tool_result", name: "deal_impact", output: { recommendation: form.state === "IL" ? "CAUTION" : "OK", warnings: form.state === "IL" ? [`${form.state} exposure would increase`] : [] } },
  );

  if (form.state === "IL") {
    events.push(
      { type: "message_delta", content: `Portfolio impact raises a **caution** — ` },
      { type: "message_delta", content: `adding another ${form.state} property increases state concentration. ` },
      { type: "message_delta", content: `Consider diversifying to CO or NV.\n` },
    );
  } else {
    events.push(
      { type: "message_delta", content: `Portfolio impact is **OK** — this ${form.state} acquisition improves diversification.\n` },
    );
  }

  const memoContent = [
    `# Deal Evaluation: ${addr}`,
    `\n**Decision: ${decision}** ${decision === "GO" ? "✅" : "⚠️"}`,
    `\n## Property`,
    `- Type: ${pt?.label}`,
    `- Address: ${addr}`,
    `- State: ${form.state}`,
    `- Purchase Price: $${price.toLocaleString()}`,
    `- NOI: $${noi.toLocaleString()}`,
    `\n## Metrics`,
    `- Cap Rate: ${cap}%`,
    `- DSCR: ${dscr}`,
    `- IRR: ${irr}%`,
    ...(hasGaming ? [`- Gaming NW (p50): $20,800/mo`] : []),
    `\n## Pipeline Stages`,
    `All 7 stages completed successfully.`,
    form.state === "IL"
      ? `\n## ⚠️ Portfolio Warning\nState concentration in IL is elevated.`
      : `\n## Portfolio Impact\nDiversification improved.`,
  ].join("\n");

  events.push(
    {
      type: "artifact_created",
      artifact: {
        id: `a_${Date.now()}`,
        title: `Deal Memo — ${addr.split(",")[0]}`,
        type: "markdown",
        content: memoContent,
        pinned: false,
      },
    },
    { type: "final_message" },
  );

  return events;
}
