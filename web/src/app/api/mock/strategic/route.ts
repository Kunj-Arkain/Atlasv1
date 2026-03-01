import { NextRequest, NextResponse } from "next/server";

export const runtime = "edge";

/* ───────────────────────────────────────────────────────────
   Mock Strategic Pipeline — server-simulated 5-stage results.
   Returns deterministic results based on input heuristics.
   Replace with: NEXT_PUBLIC_API_BASE_URL=http://api:8000
   ─────────────────────────────────────────────────────────── */

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function mockAnalyze(body: Record<string, unknown>) {
  const text = String(body.scenario_text || "");
  const title = String(body.title || "Untitled");
  const objectives = (body.objectives as string[]) || [];
  const constraints = (body.constraints as string[]) || [];
  const budget = Number(body.budget_usd) || 0;
  const risk = String(body.risk_tolerance || "moderate");
  const h = hash(text);

  const hasObjectives = objectives.filter((o) => o.trim()).length > 0;
  const hasConstraints = constraints.filter((c) => c.trim()).length > 0;
  const isDetailed = text.length > 100;

  // Confidence from completeness
  let conf = 0.45;
  if (hasObjectives) conf += 0.12;
  if (hasConstraints) conf += 0.08;
  if (budget > 0) conf += 0.08;
  if (isDetailed) conf += 0.1;
  conf = Math.min(0.95, Math.max(0.15, conf + (h % 10) / 100));

  // Decision
  let decision = "MODIFY";
  if (conf >= 0.65 && risk !== "conservative") decision = "GO";
  if (conf < 0.3) decision = "NO_GO";
  if (risk === "aggressive" && conf >= 0.5) decision = "GO";
  if (risk === "conservative" && conf < 0.7) decision = "MODIFY";

  const probs = { conservative: [0.5, 0.15, 0.35], moderate: [0.5, 0.25, 0.25], aggressive: [0.5, 0.35, 0.15] };
  const [pBase, pBull, pBear] = (probs as Record<string, number[]>)[risk] || probs.moderate;

  const missing: string[] = [];
  if (!hasObjectives) missing.push("Explicit objectives not stated");
  if (!hasConstraints) missing.push("Constraints not defined");
  if (!budget) missing.push("Budget/capital requirement not specified");
  if (text.length < 100) missing.push("Scenario description is very brief");

  // Detect domains in text
  const tl = text.toLowerCase();
  const fm: Record<string, unknown>[] = [];
  if (/regul|permit|compliance|license|legal/.test(tl))
    fm.push({ domain: "regulatory", description: "Regulatory change blocks or delays initiative", probability: "high", severity: "critical", mitigation: "Engage regulatory counsel early; build compliance timeline" });
  if (/market|demand|compet|customer|price/.test(tl))
    fm.push({ domain: "market", description: "Market conditions shift unfavorably", probability: "medium", severity: "major", mitigation: "Monitor leading indicators; build demand sensitivity into plan" });
  if (/cost|budget|fund|capital|revenue|financ/.test(tl))
    fm.push({ domain: "financial", description: "Cost overrun or funding shortfall", probability: "medium", severity: "major", mitigation: "Build contingency buffer; secure committed financing" });
  if (/partner|vendor|tenant|operator/.test(tl))
    fm.push({ domain: "partner", description: "Key partner/vendor fails to deliver", probability: "medium", severity: "major", mitigation: "Diversify partner base; negotiate performance guarantees" });
  fm.push({ domain: "execution", description: "General execution risk", probability: "medium", severity: "major", mitigation: "Clear ownership, milestones, and escalation paths" });

  const leverage = [
    hasObjectives ? `Focus on highest-ROI objective: ${objectives[0]?.slice(0, 60)}` : "Define clear primary objective to focus resources",
    "Secure early wins to build momentum and stakeholder confidence",
    "Negotiate key terms before committing capital",
    "Build optionality — structure for pivot if assumptions prove wrong",
  ];

  const actions: Record<string, unknown>[] = [];
  if (decision !== "NO_GO") {
    missing.slice(0, 2).forEach((m) =>
      actions.push({ action: `Resolve: ${m}`, owner: "Analyst", timeline: "1 week", priority: "high" })
    );
    actions.push({ action: "Draft execution plan with milestones", owner: "Project lead", timeline: "1 week", priority: "high" });
    actions.push({ action: "Negotiate key terms before commitment", owner: "Deal lead", timeline: "2 weeks", priority: "medium" });
  } else {
    actions.push({ action: "Document decision rationale", owner: "Analyst", timeline: "This week", priority: "medium" });
    actions.push({ action: "Identify alternative approaches", owner: "Strategy lead", timeline: "2 weeks", priority: "medium" });
  }

  return {
    scenario_id: `scen_${h.toString(16).slice(0, 8)}`,
    run_id: `srun_${Date.now().toString(36)}`,
    title,
    decision,
    confidence: Math.round(conf * 1000) / 1000,
    decision_rationale: decision === "GO"
      ? `Proceed with '${title}'. Sufficient information and manageable risk profile.`
      : decision === "MODIFY"
        ? `'${title}' has merit but requires modification. ${missing.length} information gap(s) remain.`
        : `'${title}' does not meet threshold. ${fm.length} risk factor(s) identified.`,
    swot: {
      strengths: [
        hasObjectives ? `Clear objective: ${objectives[0]?.slice(0, 60)}` : "Structured analysis completed",
        leverage[0],
        "Dedicated analysis pipeline applied",
      ],
      weaknesses: missing.slice(0, 2),
      opportunities: [
        `Bull case: objectives exceeded with ${(pBull * 100).toFixed(0)}% probability`,
        "Early mover advantage if executed swiftly",
      ],
      threats: fm.slice(0, 3).map((f) => `${(f.domain as string).charAt(0).toUpperCase() + (f.domain as string).slice(1)}: ${f.description}`),
    },
    scenarios: [
      { name: "base", probability: pBase, expected_outcome: "Primary objectives achieved within expected parameters", key_assumptions: ["Current conditions persist"] },
      { name: "bull", probability: pBull, expected_outcome: "Objectives exceeded; accelerated timeline and improved returns", key_assumptions: ["Favorable market conditions"] },
      { name: "bear", probability: pBear, expected_outcome: "Significant risk to primary objective; potential capital impairment", key_assumptions: ["Market deterioration"] },
    ],
    sensitivities: [
      "Revenue/demand assumptions",
      "Cost escalation risk",
      "Timeline delays",
      "Regulatory changes",
      "Competitive response",
    ],
    failure_modes: fm,
    second_order_effects: [
      "Competitor response to our actions is unknown",
      "Market perception / reputation effects of decision",
      "Resource allocation affects other initiatives",
    ],
    leverage_points: leverage,
    missing_info: missing,
    contradictions: objectives.length > 2 && constraints.length > 2
      ? ["Many objectives with many constraints may create feasibility tension"]
      : [],
    next_actions: actions,
    stage_results: {
      compression: { status: "pass" },
      decision_prep: { status: "pass", preliminary_decision: decision },
      scenarios: { status: "pass" },
      patterns: { status: "pass" },
      synthesis: { status: "pass" },
    },
    stage_routes: {
      compression: "cheap_structured",
      decision_prep: "cheap_structured",
      scenarios: "strategic_deep",
      patterns: "strategic_deep",
      synthesis: "strategic_deep",
    },
    elapsed_ms: 12 + (h % 30),
    llm_cost_usd: 0,
    status: "completed",
  };
}

export async function POST(req: NextRequest) {
  const url = new URL(req.url);
  const body = await req.json();

  // Route sub-endpoints via query param or path
  const sub = url.searchParams.get("sub") || "analyze";

  if (sub === "analyze" || sub === "swot" || sub === "scenarios" || sub === "stress-test") {
    const result = mockAnalyze(body);

    if (sub === "swot") return NextResponse.json(result.swot);
    if (sub === "scenarios") return NextResponse.json({
      scenarios: result.scenarios,
      sensitivities: result.sensitivities,
      second_order_effects: result.second_order_effects,
    });
    if (sub === "stress-test") return NextResponse.json({
      decision: result.decision,
      failure_modes: result.failure_modes,
      contradictions: result.contradictions,
      second_order_effects: result.second_order_effects,
      confidence: result.confidence,
    });

    return NextResponse.json(result);
  }

  if (sub === "memo") {
    return NextResponse.json({
      artifact_id: `art_${Date.now().toString(36)}`,
      type: "markdown",
      path: "/artifacts/memo.md",
    });
  }

  if (sub === "research") {
    const address = String(body.address || "456 Oak Ave, Springfield, IL");
    const propType = String(body.property_type || "gas_station");
    return NextResponse.json({
      report: {
        executive_summary: `Market research for ${address} indicates a moderate opportunity. The area shows stable demographics and manageable competition. Further due diligence recommended on gaming terminal performance and regulatory status.`,
        site_score: 7,
        site_grade: "B",
        demographics: {
          population: "~115,000 (Springfield metro ~210,000)",
          median_income: "$52,400",
          growth_trend: "stable",
          key_facts: ["County seat of Sangamon County", "State capital provides stable employment base", "Median age 36.2"],
        },
        traffic_access: {
          estimated_daily_traffic: "12,000-18,000 AADT (estimated)",
          highway_proximity: "Near I-55 / IL-4 interchange",
          accessibility_score: 7,
          key_facts: ["High-traffic commercial corridor", "Good visibility from main road"],
        },
        competition: {
          direct_competitors_nearby: 4,
          competitor_names: ["BP (0.3mi)", "Shell (0.5mi)", "Casey's (0.8mi)", "Circle K (1.1mi)"],
          market_saturation: "moderate",
          key_facts: ["4 gas stations within 1-mile radius", "Casey's and Circle K also offer VGT"],
        },
        gaming_market: {
          state_gaming_revenue_trend: "growing",
          local_terminal_count: "~850 terminals in Sangamon County",
          avg_nti_per_terminal: "$18,200/yr (state avg)",
          regulatory_outlook: "favorable",
          key_facts: ["IL video gaming growing 6% YoY", "Terminal cap: 6 per location", "Tax rate: 34% effective"],
        },
        real_estate: {
          comparable_sales: ["Gas station + C-store sold $780K (2024, 0.5mi)", "Similar property $920K (2024, 2.1mi)"],
          estimated_cap_rate: "7.5-8.5% (C-store/gas station)",
          valuation_assessment: "Asking price appears in-line with comps",
          key_facts: ["NOI of $72K implies ~8.5% cap rate at $850K"],
        },
        regulatory: {
          zoning_status: "compatible",
          license_requirements: ["IL Video Gaming Terminal Operator License", "Local liquor license (if applicable)", "EPA compliance for UST"],
          key_facts: ["Springfield has approved gaming at similar locations"],
        },
        economic_outlook: {
          employment_trend: "stable",
          development_activity: "moderate",
          key_facts: ["State government anchor employer", "New warehouse development on south side"],
        },
        risk_factors: [
          { risk: "Gaming terminal saturation in immediate area", severity: "medium", mitigation: "Focus on location quality and customer experience" },
          { risk: "Fuel margin compression from EV adoption", severity: "low", mitigation: "Diversify revenue toward C-store and gaming" },
          { risk: "Environmental liability from underground tanks", severity: "medium", mitigation: "Phase I/II environmental assessment required" },
        ],
        data_gaps: [
          "Exact daily traffic count for this specific address",
          "Current operator's gaming terminal revenue (NTI)",
          "Underground storage tank inspection history",
        ],
        recommendations: [
          "Obtain actual NTI data from current operator or IL Gaming Board FOIA",
          "Commission Phase I environmental assessment",
          "Verify gaming license transferability with IL Gaming Board",
          "Negotiate based on verifiable NOI, not asking price",
          "Model scenarios with 5 and 6 terminals to find break-even",
        ],
        sources_consulted: 18,
        _meta: { address, queries_executed: 18, elapsed_ms: 450, llm_cost_usd: 0 },
      },
    });
  }

  return NextResponse.json({ error: "Unknown sub-endpoint" }, { status: 400 });
}

export async function GET() {
  // GET /strategic — returns templates and recent runs
  return NextResponse.json({
    templates: [
      { template_type: "acquisition", name: "Acquisition Analysis" },
      { template_type: "expansion", name: "Market Expansion" },
      { template_type: "partnership", name: "Partnership / JV" },
      { template_type: "gaming", name: "Gaming Expansion" },
      { template_type: "general", name: "General Strategy" },
    ],
    runs: [],
  });
}
