import { NextResponse } from "next/server";
import type { Artifact } from "@/lib/contracts";

const artifacts: Artifact[] = [
  {
    id: "a1", title: "Deal Memo — 123 Main St", type: "markdown", pinned: true, threadId: "t1",
    content: "# Deal Evaluation: 123 Main St Strip Center\n\n**Decision: GO**\n\n## Metrics\n- Purchase Price: $1,500,000\n- NOI: $120,000\n- Cap Rate: 8.0%\n- DSCR: 1.25\n- IRR: 15.2%\n\n## Stage Results\n| Stage | Score | Status |\n|-------|-------|--------|\n| Intake | — | ✅ Pass |\n| Feasibility | 1.00 | ✅ Pass |\n| Market | 0.70 | ✅ Pass |\n| Cost | 1.00 | ✅ Pass |\n| Finance | 0.85 | ✅ Pass |\n| Risk | 0.75 | ✅ Pass |\n| Decision | 0.83 | **GO** |",
  },
  {
    id: "a2", title: "Monte Carlo — BP #12", type: "json", pinned: false, threadId: "t2",
    content: JSON.stringify({ irr_p10: 0.956, irr_p50: 1.60, irr_p90: 2.575, net_win_p50: 20688, operator_cf_p50: 13447, breakeven_net_win: 2564 }, null, 2),
  },
  {
    id: "a3", title: "Portfolio Dashboard", type: "json", pinned: true, threadId: "t3",
    content: JSON.stringify({ total_assets: 4, total_value: 3770000, total_debt: 870000, leverage_ratio: 0.2308, state_hhi: 0.6847 }, null, 2),
  },
];

export async function GET(req: Request) {
  const url = new URL(req.url);
  const threadId = url.searchParams.get("threadId");
  const q = url.searchParams.get("q");

  let result = artifacts;
  if (threadId) result = result.filter((a) => a.threadId === threadId);
  if (q) result = result.filter((a) => a.title.toLowerCase().includes(q.toLowerCase()));

  return NextResponse.json(result);
}

export async function POST(req: Request) {
  const body = await req.json();
  const artifact: Artifact = { id: `a_${Date.now()}`, pinned: false, ...body };
  artifacts.unshift(artifact);
  return NextResponse.json(artifact, { status: 201 });
}
