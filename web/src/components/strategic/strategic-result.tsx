"use client";

import { useState } from "react";
import {
  Brain, ChevronDown, ChevronRight, AlertTriangle, CheckCircle2,
  XCircle, Target, Shield, TrendingUp, TrendingDown, Minus,
  Lightbulb, Clock, FileText,
} from "lucide-react";

export interface StrategicResult {
  title: string;
  decision: string;
  confidence: number;
  decision_rationale: string;
  swot: { strengths: string[]; weaknesses: string[]; opportunities: string[]; threats: string[] };
  scenarios: { name: string; probability: number; expected_outcome: string; key_assumptions: string[] }[];
  sensitivities: string[];
  failure_modes: { domain: string; description: string; probability: string; severity: string; mitigation: string }[];
  second_order_effects: string[];
  leverage_points: string[];
  missing_info: string[];
  contradictions: string[];
  next_actions: { action: string; owner: string; timeline: string; priority: string }[];
  stage_routes: Record<string, string>;
  elapsed_ms: number;
}

interface StrategicResultPanelProps {
  result: StrategicResult;
  onExportMemo?: () => void;
}

function Section({ title, icon: Icon, children, defaultOpen = true }: {
  title: string; icon: any; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="mb-3">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 py-2 text-left"
      >
        {open ? <ChevronDown size={13} className="text-[var(--text-dim)]" /> : <ChevronRight size={13} className="text-[var(--text-dim)]" />}
        <Icon size={14} className="text-[var(--accent)]" />
        <span className="text-[13px] font-bold">{title}</span>
      </button>
      {open && <div className="pl-7">{children}</div>}
    </div>
  );
}

export function StrategicResultPanel({ result, onExportMemo }: StrategicResultPanelProps) {
  const decisionConfig: Record<string, { color: string; icon: any; bg: string }> = {
    GO: { color: "var(--green)", icon: CheckCircle2, bg: "rgba(22,163,74,0.1)" },
    MODIFY: { color: "var(--yellow)", icon: AlertTriangle, bg: "rgba(202,138,4,0.1)" },
    NO_GO: { color: "var(--red)", icon: XCircle, bg: "rgba(220,38,38,0.1)" },
  };

  const dc = decisionConfig[result.decision] || decisionConfig.MODIFY;
  const DecIcon = dc.icon;

  const scenarioIcon: Record<string, any> = {
    bull: TrendingUp, base: Minus, bear: TrendingDown,
  };

  return (
    <div className="flex-1 overflow-auto p-4">
      {/* Decision Header */}
      <div className="rounded-xl p-4 mb-4 border-2" style={{ borderColor: dc.color, background: dc.bg }}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <DecIcon size={20} style={{ color: dc.color }} />
            <span className="text-[18px] font-bold" style={{ color: dc.color }}>
              {result.decision.replace("_", "-")}
            </span>
          </div>
          <div className="text-right">
            <div className="text-[11px] text-[var(--text-dim)]">Confidence</div>
            <div className="text-[18px] font-bold" style={{ color: dc.color }}>
              {(result.confidence * 100).toFixed(0)}%
            </div>
          </div>
        </div>
        <div className="text-[13px] text-[var(--text-muted)] leading-relaxed">
          {result.decision_rationale}
        </div>
        <div className="flex items-center justify-between mt-3 text-[11px] text-[var(--text-dim)]">
          <span>{result.elapsed_ms}ms · {Object.keys(result.stage_routes).length} stages</span>
          {onExportMemo && (
            <button
              onClick={onExportMemo}
              className="flex items-center gap-1 text-[var(--accent)] hover:underline"
            >
              <FileText size={11} /> Export Memo
            </button>
          )}
        </div>
      </div>

      {/* SWOT */}
      <Section title="SWOT Analysis" icon={Target} defaultOpen={true}>
        <div className="grid grid-cols-2 gap-2 mb-3">
          {(["strengths", "weaknesses", "opportunities", "threats"] as const).map((q) => {
            const colors: Record<string, string> = {
              strengths: "var(--green)", weaknesses: "var(--red)",
              opportunities: "var(--accent)", threats: "var(--orange)",
            };
            const items = result.swot[q] || [];
            return (
              <div key={q} className="bg-[var(--hover)] rounded-lg p-3 border border-[var(--border)]">
                <div className="text-[10px] font-bold uppercase tracking-wider mb-1.5" style={{ color: colors[q] }}>
                  {q}
                </div>
                {items.length > 0 ? items.map((item, i) => (
                  <div key={i} className="text-[12px] text-[var(--text-muted)] mb-1 leading-snug">
                    • {item}
                  </div>
                )) : (
                  <div className="text-[11px] text-[var(--text-dim)] italic">None identified</div>
                )}
              </div>
            );
          })}
        </div>
      </Section>

      {/* Scenarios */}
      <Section title={`Scenario Cases (${result.scenarios.length})`} icon={TrendingUp}>
        <div className="space-y-2 mb-3">
          {result.scenarios.map((sc, i) => {
            const ScIcon = scenarioIcon[sc.name] || Minus;
            const colors: Record<string, string> = {
              bull: "var(--green)", base: "var(--accent)", bear: "var(--red)",
            };
            return (
              <div key={i} className="bg-[var(--hover)] rounded-lg p-3 border border-[var(--border)]">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-1.5">
                    <ScIcon size={13} style={{ color: colors[sc.name] || "var(--text)" }} />
                    <span className="text-[13px] font-bold capitalize">{sc.name}</span>
                  </div>
                  <span className="text-xs font-semibold" style={{ color: colors[sc.name] }}>
                    {(sc.probability * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="text-[12px] text-[var(--text-muted)] leading-snug">{sc.expected_outcome}</div>
              </div>
            );
          })}
        </div>
        {result.sensitivities.length > 0 && (
          <div className="mb-3">
            <div className="text-[11px] font-semibold text-[var(--text-dim)] mb-1">Key Sensitivities:</div>
            {result.sensitivities.map((s, i) => (
              <div key={i} className="text-[12px] text-[var(--text-muted)] mb-0.5">• {s}</div>
            ))}
          </div>
        )}
      </Section>

      {/* Failure Modes */}
      <Section title={`Failure Modes (${result.failure_modes.length})`} icon={AlertTriangle}>
        <div className="space-y-2 mb-3">
          {result.failure_modes.map((fm, i) => {
            const sevColor: Record<string, string> = {
              critical: "var(--red)", major: "var(--orange)", minor: "var(--yellow)",
            };
            return (
              <div key={i} className="bg-[var(--hover)] rounded-lg p-3 border border-[var(--border)]">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded"
                    style={{ background: `${sevColor[fm.severity] || "var(--text-dim)"}20`, color: sevColor[fm.severity] }}>
                    {fm.severity}
                  </span>
                  <span className="text-[12px] font-semibold capitalize">{fm.domain}</span>
                  <span className="text-[10px] text-[var(--text-dim)]">({fm.probability})</span>
                </div>
                <div className="text-[12px] text-[var(--text-muted)] mb-1">{fm.description}</div>
                <div className="text-[11px] text-[var(--teal)]">↳ {fm.mitigation}</div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Leverage Points */}
      {result.leverage_points.length > 0 && (
        <Section title={`Leverage Points (${result.leverage_points.length})`} icon={Lightbulb}>
          <div className="mb-3">
            {result.leverage_points.map((lp, i) => (
              <div key={i} className="text-[12px] text-[var(--text-muted)] mb-1.5 flex gap-2">
                <span className="text-[var(--accent)]">▸</span> {lp}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Second-Order Effects */}
      {result.second_order_effects.length > 0 && (
        <Section title="Second-Order Effects" icon={Brain} defaultOpen={false}>
          <div className="mb-3">
            {result.second_order_effects.map((e, i) => (
              <div key={i} className="text-[12px] text-[var(--text-muted)] mb-1">• {e}</div>
            ))}
          </div>
        </Section>
      )}

      {/* Gaps & Contradictions */}
      {(result.missing_info.length > 0 || result.contradictions.length > 0) && (
        <Section title="Gaps & Contradictions" icon={Shield} defaultOpen={false}>
          <div className="mb-3">
            {result.missing_info.map((m, i) => (
              <div key={i} className="text-[12px] text-[var(--yellow)] mb-1">⚠ {m}</div>
            ))}
            {result.contradictions.map((c, i) => (
              <div key={i} className="text-[12px] text-[var(--orange)] mb-1">⚡ {c}</div>
            ))}
          </div>
        </Section>
      )}

      {/* Next Actions */}
      <Section title={`Next Actions (${result.next_actions.length})`} icon={Clock}>
        <div className="mb-3">
          {result.next_actions.map((a, i) => {
            const prioColor: Record<string, string> = {
              high: "var(--red)", medium: "var(--yellow)", low: "var(--green)",
            };
            return (
              <div key={i} className="flex items-start gap-2 py-2 border-b border-[var(--border-light)] last:border-0">
                <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0"
                  style={{ background: prioColor[a.priority] || "var(--text-dim)" }} />
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] text-[var(--text)]">{a.action}</div>
                  <div className="text-[10px] text-[var(--text-dim)]">
                    {a.owner} · {a.timeline}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Stage Routes (Phase 7) */}
      <Section title="Pipeline Routing" icon={Brain} defaultOpen={false}>
        <div className="mb-3">
          {Object.entries(result.stage_routes).map(([stage, route]) => (
            <div key={stage} className="flex justify-between py-1.5 text-[12px]">
              <span className="text-[var(--text-muted)]">{stage}</span>
              <code className="text-[11px] bg-[var(--hover)] px-1.5 py-0.5 rounded text-[var(--accent)]">{route}</code>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
