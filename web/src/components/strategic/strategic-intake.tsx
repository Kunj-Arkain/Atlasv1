"use client";

import { useState } from "react";
import {
  X, ChevronLeft, ArrowRight, Brain, Zap,
  Target, Shield, Clock, DollarSign,
} from "lucide-react";

export interface StrategicInput {
  title: string;
  scenario_text: string;
  template_type: string;
  objectives: string[];
  constraints: string[];
  time_horizon: string;
  budget_usd: string;
  risk_tolerance: string;
  assumptions: string[];
}

interface StrategicIntakeProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (input: StrategicInput) => void;
}

const TEMPLATES = [
  { id: "acquisition", label: "Acquisition Analysis", icon: DollarSign, color: "#6366f1", desc: "Evaluate a potential acquisition target" },
  { id: "expansion", label: "Market Expansion", icon: Target, color: "#22c55e", desc: "Entry into a new market or geography" },
  { id: "partnership", label: "Partnership / JV", icon: Shield, color: "#f97316", desc: "Partnership or joint venture evaluation" },
  { id: "gaming", label: "Gaming Expansion", icon: Zap, color: "#14b8a6", desc: "Gaming terminal expansion or new venue" },
  { id: "general", label: "General Strategy", icon: Brain, color: "#8b5cf6", desc: "Open-ended strategic decision analysis" },
];

const HORIZONS = ["short", "medium", "long", "strategic"];
const RISK_LEVELS = ["conservative", "moderate", "aggressive"];

export function StrategicIntake({ open, onClose, onSubmit }: StrategicIntakeProps) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<StrategicInput>({
    title: "",
    scenario_text: "",
    template_type: "general",
    objectives: [""],
    constraints: [""],
    time_horizon: "medium",
    budget_usd: "",
    risk_tolerance: "moderate",
    assumptions: [""],
  });

  if (!open) return null;

  const set = <K extends keyof StrategicInput>(k: K, v: StrategicInput[K]) =>
    setForm((p) => ({ ...p, [k]: v }));

  const updateList = (key: "objectives" | "constraints" | "assumptions", idx: number, val: string) => {
    const arr = [...form[key]];
    arr[idx] = val;
    set(key, arr);
  };

  const addToList = (key: "objectives" | "constraints" | "assumptions") => {
    set(key, [...form[key], ""]);
  };

  const removeFromList = (key: "objectives" | "constraints" | "assumptions", idx: number) => {
    set(key, form[key].filter((_, i) => i !== idx));
  };

  const stepLabels = ["Template", "Scenario", "Objectives", "Parameters", "Review"];
  const totalSteps = 5;

  const canNext = () => {
    if (step === 0) return !!form.template_type;
    if (step === 1) return !!form.title.trim() && form.scenario_text.trim().length >= 10;
    return true;
  };

  const handleSubmit = () => {
    const clean = {
      ...form,
      objectives: form.objectives.filter((o) => o.trim()),
      constraints: form.constraints.filter((c) => c.trim()),
      assumptions: form.assumptions.filter((a) => a.trim()),
    };
    onSubmit(clean);
    setStep(0);
    setForm({
      title: "", scenario_text: "", template_type: "general",
      objectives: [""], constraints: [""], time_horizon: "medium",
      budget_usd: "", risk_tolerance: "moderate", assumptions: [""],
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      <div className="relative bg-[var(--surface)] rounded-2xl w-[720px] max-h-[88vh] overflow-hidden flex flex-col border border-[var(--border)] shadow-2xl">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[var(--border)] flex justify-between items-center">
          <div>
            <div className="text-[17px] font-bold tracking-tight flex items-center gap-2">
              <Brain size={18} className="text-[var(--accent)]" />
              Strategic Analysis
            </div>
            <div className="text-xs text-[var(--text-dim)] mt-0.5">
              5-stage cognitive pipeline · SWOT · Scenario modeling · Risk analysis
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)]">
            <X size={18} />
          </button>
        </div>

        {/* Progress */}
        <div className="flex px-6 py-3 gap-1 border-b border-[var(--border-light)]">
          {stepLabels.map((label, i) => (
            <div key={i} className="flex-1 flex flex-col items-center gap-1">
              <div
                className="w-full h-[3px] rounded-sm transition-all duration-300"
                style={{ background: i <= step ? "var(--accent)" : "var(--hover)" }}
              />
              <span className="text-[10px]" style={{
                color: i <= step ? "var(--accent)" : "var(--text-dim)",
                fontWeight: i === step ? 700 : 400,
              }}>
                {label}
              </span>
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-6">
          {/* Step 0: Template */}
          {step === 0 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Analysis Template</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">
                Choose a template to pre-configure stage routes and weight presets.
              </p>
              <div className="grid grid-cols-2 gap-2.5">
                {TEMPLATES.map((t) => {
                  const Icon = t.icon;
                  const sel = form.template_type === t.id;
                  return (
                    <button
                      key={t.id}
                      onClick={() => set("template_type", t.id)}
                      className="flex flex-col items-start gap-2 p-4 rounded-xl border-2 transition-all text-left"
                      style={{
                        borderColor: sel ? t.color : "var(--border)",
                        background: sel ? `${t.color}11` : "var(--hover)",
                      }}
                    >
                      <div className="flex items-center gap-2" style={{ color: sel ? t.color : "var(--text)" }}>
                        <Icon size={18} />
                        <span className="font-bold text-[13px]">{t.label}</span>
                      </div>
                      <span className="text-[11px] text-[var(--text-dim)]">{t.desc}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Step 1: Scenario */}
          {step === 1 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Describe the Scenario</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">
                Provide the strategic situation to analyze. Be specific about context, stakeholders, and what&apos;s at stake.
              </p>
              <div className="space-y-3.5">
                <div>
                  <label className="text-xs text-[var(--text-muted)] font-semibold block mb-1">
                    Title <span className="text-[var(--red)]">*</span>
                  </label>
                  <input
                    value={form.title}
                    onChange={(e) => set("title", e.target.value)}
                    placeholder="e.g. Expand into Nevada gaming market"
                    className="w-full bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-dim)]"
                  />
                </div>
                <div>
                  <label className="text-xs text-[var(--text-muted)] font-semibold block mb-1">
                    Scenario Narrative <span className="text-[var(--red)]">*</span>
                  </label>
                  <textarea
                    value={form.scenario_text}
                    onChange={(e) => set("scenario_text", e.target.value)}
                    placeholder="Describe the situation, context, key players, and what decision needs to be made..."
                    rows={6}
                    className="w-full bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-dim)] resize-none"
                  />
                  <div className="text-[10px] text-[var(--text-dim)] mt-1 text-right">
                    {form.scenario_text.length} chars
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Step 2: Objectives & Constraints */}
          {step === 2 && (
            <div className="space-y-5">
              <div>
                <h3 className="text-[15px] font-bold mb-1">Objectives</h3>
                <p className="text-[13px] text-[var(--text-muted)] mb-3">What must this initiative achieve?</p>
                {form.objectives.map((obj, i) => (
                  <div key={i} className="flex gap-2 mb-2">
                    <input
                      value={obj}
                      onChange={(e) => updateList("objectives", i, e.target.value)}
                      placeholder={`Objective ${i + 1}`}
                      className="flex-1 bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-dim)]"
                    />
                    {form.objectives.length > 1 && (
                      <button onClick={() => removeFromList("objectives", i)} className="text-[var(--text-dim)] hover:text-[var(--red)] text-xs px-2">✕</button>
                    )}
                  </div>
                ))}
                <button onClick={() => addToList("objectives")} className="text-xs text-[var(--accent)] hover:underline">+ Add objective</button>
              </div>

              <div>
                <h3 className="text-[15px] font-bold mb-1">Constraints</h3>
                <p className="text-[13px] text-[var(--text-muted)] mb-3">What limits or boundaries apply?</p>
                {form.constraints.map((con, i) => (
                  <div key={i} className="flex gap-2 mb-2">
                    <input
                      value={con}
                      onChange={(e) => updateList("constraints", i, e.target.value)}
                      placeholder={`Constraint ${i + 1}`}
                      className="flex-1 bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-dim)]"
                    />
                    {form.constraints.length > 1 && (
                      <button onClick={() => removeFromList("constraints", i)} className="text-[var(--text-dim)] hover:text-[var(--red)] text-xs px-2">✕</button>
                    )}
                  </div>
                ))}
                <button onClick={() => addToList("constraints")} className="text-xs text-[var(--accent)] hover:underline">+ Add constraint</button>
              </div>
            </div>
          )}

          {/* Step 3: Parameters */}
          {step === 3 && (
            <div className="space-y-5">
              <h3 className="text-[15px] font-bold mb-1">Analysis Parameters</h3>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-[var(--text-muted)] font-semibold block mb-1">
                    <Clock size={12} className="inline mr-1" /> Time Horizon
                  </label>
                  <div className="flex gap-1.5">
                    {HORIZONS.map((h) => (
                      <button
                        key={h}
                        onClick={() => set("time_horizon", h)}
                        className="flex-1 py-2 rounded-lg text-xs font-semibold transition-all border"
                        style={{
                          borderColor: form.time_horizon === h ? "var(--accent)" : "var(--border)",
                          background: form.time_horizon === h ? "var(--accent)" : "var(--hover)",
                          color: form.time_horizon === h ? "white" : "var(--text-muted)",
                        }}
                      >
                        {h.charAt(0).toUpperCase() + h.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="text-xs text-[var(--text-muted)] font-semibold block mb-1">
                    <DollarSign size={12} className="inline mr-1" /> Budget / Capital ($)
                  </label>
                  <input
                    value={form.budget_usd}
                    onChange={(e) => set("budget_usd", e.target.value)}
                    placeholder="e.g. 1500000"
                    inputMode="numeric"
                    className="w-full bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-dim)]"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs text-[var(--text-muted)] font-semibold block mb-2">
                  <Shield size={12} className="inline mr-1" /> Risk Tolerance
                </label>
                <div className="flex gap-2">
                  {RISK_LEVELS.map((r) => (
                    <button
                      key={r}
                      onClick={() => set("risk_tolerance", r)}
                      className="flex-1 py-3 rounded-xl border-2 text-[13px] font-semibold transition-all"
                      style={{
                        borderColor: form.risk_tolerance === r ? "var(--accent)" : "var(--border)",
                        background: form.risk_tolerance === r ? "var(--accent)/10" : "var(--hover)",
                        color: form.risk_tolerance === r ? "var(--accent)" : "var(--text)",
                      }}
                    >
                      {r.charAt(0).toUpperCase() + r.slice(1)}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <h4 className="text-xs text-[var(--text-muted)] font-semibold mb-2">Working Assumptions (optional)</h4>
                {form.assumptions.map((a, i) => (
                  <div key={i} className="flex gap-2 mb-2">
                    <input
                      value={a}
                      onChange={(e) => updateList("assumptions", i, e.target.value)}
                      placeholder={`Assumption ${i + 1}`}
                      className="flex-1 bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-dim)]"
                    />
                    {form.assumptions.length > 1 && (
                      <button onClick={() => removeFromList("assumptions", i)} className="text-[var(--text-dim)] hover:text-[var(--red)] text-xs px-2">✕</button>
                    )}
                  </div>
                ))}
                <button onClick={() => addToList("assumptions")} className="text-xs text-[var(--accent)] hover:underline">+ Add assumption</button>
              </div>
            </div>
          )}

          {/* Step 4: Review */}
          {step === 4 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Review & Submit</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">Confirm before running the 5-stage strategic pipeline.</p>

              <div className="bg-[var(--hover)] rounded-xl border border-[var(--border)] overflow-hidden">
                {([
                  ["Template", TEMPLATES.find((t) => t.id === form.template_type)?.label],
                  ["Title", form.title],
                  ["Scenario", form.scenario_text.slice(0, 100) + (form.scenario_text.length > 100 ? "..." : "")],
                  ["Objectives", form.objectives.filter((o) => o.trim()).join("; ") || "—"],
                  ["Constraints", form.constraints.filter((c) => c.trim()).join("; ") || "—"],
                  ["Time Horizon", form.time_horizon],
                  ["Budget", form.budget_usd ? `$${Number(form.budget_usd).toLocaleString()}` : "—"],
                  ["Risk Tolerance", form.risk_tolerance],
                ] as [string, string | undefined][])
                  .filter(([, v]) => v)
                  .map(([label, val], i, arr) => (
                    <div key={i} className={`flex justify-between px-4 py-2.5 ${i < arr.length - 1 ? "border-b border-[var(--border-light)]" : ""}`}>
                      <span className="text-[13px] text-[var(--text-muted)]">{label}</span>
                      <span className="text-[13px] font-semibold max-w-[350px] text-right">{val}</span>
                    </div>
                  ))}
              </div>

              <div className="mt-4 p-3.5 rounded-xl bg-[var(--accent)]/10 border border-[var(--accent)]/20">
                <div className="text-xs text-[var(--accent)] font-semibold mb-1">Pipeline stages:</div>
                <div className="text-xs text-[var(--text-muted)] leading-relaxed">
                  Compression → Decision Prep → Scenarios → Pattern Analysis → Synthesis
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3.5 border-t border-[var(--border)] flex justify-between">
          <button
            onClick={() => (step > 0 ? setStep(step - 1) : onClose())}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[var(--border)] text-[13px] text-[var(--text-muted)] hover:bg-[var(--hover)]"
          >
            <ChevronLeft size={14} /> {step === 0 ? "Cancel" : "Back"}
          </button>

          {step < totalSteps - 1 ? (
            <button
              onClick={() => canNext() && setStep(step + 1)}
              disabled={!canNext()}
              className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-[13px] font-semibold transition-all"
              style={{
                background: canNext() ? "var(--accent)" : "var(--hover)",
                color: canNext() ? "white" : "var(--text-dim)",
              }}
            >
              Next <ArrowRight size={14} />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              className="flex items-center gap-2 px-6 py-2 rounded-lg text-[13px] font-bold bg-[var(--accent)] text-white hover:opacity-90 transition-all"
            >
              <Brain size={14} /> Run Analysis
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
