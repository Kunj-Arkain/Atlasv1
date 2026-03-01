"use client";

import { useState } from "react";
import {
  X, ChevronLeft, ArrowRight, Zap, DollarSign, CreditCard, Gamepad2,
} from "lucide-react";
import type { DealIntakeForm } from "@/lib/contracts";
import {
  PROPERTY_TYPES, CORE_FIELDS, GAMING_FIELDS, FINANCING_FIELDS,
} from "@/lib/constants";
import { FieldGroup } from "./field-group";

interface IntakeModalProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: DealIntakeForm) => void;
}

export function IntakeModal({ open, onClose, onSubmit }: IntakeModalProps) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<DealIntakeForm>({
    property_type: "",
    state: "IL",
    include_gaming: false,
    financing: "cash",
  });

  if (!open) return null;

  const set = (key: string, val: string | boolean) =>
    setForm((p) => ({ ...p, [key]: val }));

  const pt = PROPERTY_TYPES[form.property_type];
  // All types have gaming step: type → core → specific → gaming → financing → review
  const totalSteps = 6;
  const stepLabels = [
    "Type",
    "Core",
    pt?.label?.split(" ")[0] || "Details",
    "Gaming",
    "Financing",
    "Review",
  ];

  const canNext = () => {
    if (step === 0) return !!form.property_type;
    if (step === 1) return !!form.purchase_price && !!form.address;
    return true;
  };

  const handleSubmit = () => {
    onSubmit(form);
    // Reset for next use
    setStep(0);
    setForm({ property_type: "", state: "IL", include_gaming: false, financing: "cash" });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-[var(--surface)] rounded-2xl w-[720px] max-h-[88vh] overflow-hidden flex flex-col border border-[var(--border)] shadow-2xl">

        {/* Header */}
        <div className="px-6 py-4 border-b border-[var(--border)] flex justify-between items-center">
          <div>
            <div className="text-[17px] font-bold tracking-tight">New Deal Evaluation</div>
            <div className="text-xs text-[var(--text-dim)] mt-0.5">
              7-stage pipeline · Monte Carlo · Portfolio impact
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-[var(--hover)] text-[var(--text-muted)] transition-colors">
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
              <span
                className="text-[10px] transition-colors"
                style={{
                  color: i <= step ? "var(--accent)" : "var(--text-dim)",
                  fontWeight: i === step ? 700 : 400,
                }}
              >
                {label}
              </span>
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-6">

          {/* Step 0: Property Type */}
          {step === 0 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Select Property Type</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">
                Each type has tailored inputs and scoring weights. All types support gaming terminal analysis.
              </p>
              <div className="grid grid-cols-3 gap-2.5">
                {Object.entries(PROPERTY_TYPES).map(([key, config]) => {
                  const Icon = config.icon;
                  const selected = form.property_type === key;
                  return (
                    <button
                      key={key}
                      onClick={() => set("property_type", key)}
                      className="flex flex-col items-start gap-2 p-4 rounded-xl border-2 transition-all text-left"
                      style={{
                        borderColor: selected ? config.color : "var(--border)",
                        background: selected ? `${config.color}11` : "var(--hover)",
                      }}
                    >
                      <div className="flex items-center gap-2" style={{ color: selected ? config.color : "var(--text)" }}>
                        <Icon size={18} />
                        <span className="font-bold text-[13px]">{config.label}</span>
                      </div>
                      <span className="text-[11px] text-[var(--text-dim)]">{config.description}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Step 1: Core */}
          {step === 1 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Core Property Details</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">Required fields for the evaluation pipeline.</p>
              <FieldGroup fields={CORE_FIELDS} values={form} onChange={(k, v) => set(k, v)} />
            </div>
          )}

          {/* Step 2: Type-specific */}
          {step === 2 && pt && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">{pt.label} Details</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">Optional fields that improve analysis accuracy.</p>
              {pt.fields.length > 0
                ? <FieldGroup fields={pt.fields} values={form} onChange={(k, v) => set(k, v)} />
                : <p className="text-[var(--text-dim)]">No additional fields for this type.</p>
              }
            </div>
          )}

          {/* Step 3: Gaming */}
          {step === 3 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Gaming / VGT Configuration</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">Configure video gaming terminal details for revenue forecasting.</p>

              {/* Toggle */}
              <div className="flex items-center gap-3 mb-4 p-3.5 rounded-xl bg-[var(--hover)] border border-[var(--border)]">
                <button
                  onClick={() => set("include_gaming", !form.include_gaming)}
                  className="relative w-11 h-6 rounded-full transition-colors"
                  style={{ background: form.include_gaming ? "var(--accent)" : "var(--border)" }}
                >
                  <div
                    className="absolute top-[3px] w-[18px] h-[18px] rounded-full bg-white transition-all"
                    style={{ left: form.include_gaming ? 23 : 3 }}
                  />
                </button>
                <div>
                  <div className="font-semibold text-[13px]">Include Gaming Analysis</div>
                  <div className="text-[11px] text-[var(--text-dim)]">Adds EGM prediction + contract simulation to pipeline</div>
                </div>
              </div>

              {form.include_gaming && (
                <FieldGroup fields={GAMING_FIELDS} values={form} onChange={(k, v) => set(k, v)} />
              )}
            </div>
          )}

          {/* Step 4: Financing */}
          {step === 4 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Financing Structure</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">How will this acquisition be funded?</p>

              <div className="flex gap-2.5 mb-4">
                {([
                  { id: "cash", label: "All Cash", Icon: DollarSign },
                  { id: "financed", label: "Financed", Icon: CreditCard },
                ] as const).map(({ id, label, Icon }) => (
                  <button
                    key={id}
                    onClick={() => set("financing", id)}
                    className="flex-1 flex items-center justify-center gap-2 py-3.5 rounded-xl border-2 font-semibold transition-all"
                    style={{
                      borderColor: form.financing === id ? "var(--accent)" : "var(--border)",
                      background: form.financing === id ? "var(--accent-muted)/10" : "var(--hover)",
                      color: form.financing === id ? "var(--accent)" : "var(--text)",
                    }}
                  >
                    <Icon size={16} /> {label}
                  </button>
                ))}
              </div>

              {form.financing === "financed" && (
                <FieldGroup fields={FINANCING_FIELDS} values={form} onChange={(k, v) => set(k, v)} />
              )}
            </div>
          )}

          {/* Step 5: Review */}
          {step === 5 && (
            <div>
              <h3 className="text-[15px] font-bold mb-1">Review & Submit</h3>
              <p className="text-[13px] text-[var(--text-muted)] mb-4">Confirm details before running the pipeline.</p>

              <div className="bg-[var(--hover)] rounded-xl border border-[var(--border)] overflow-hidden">
                {([
                  ["Property Type", pt?.label],
                  ["Address", form.address],
                  ["State", form.state],
                  ["Purchase Price", form.purchase_price ? `$${Number(form.purchase_price).toLocaleString()}` : "—"],
                  ["Annual NOI", form.noi ? `$${Number(form.noi).toLocaleString()}` : "Derived from cap rate"],
                  ["Financing", form.financing === "cash" ? "All Cash" : `Financed${form.down_payment_pct ? ` (${form.down_payment_pct}% down)` : ""}`],
                  ...(form.include_gaming ? [["Gaming", `${form.terminal_count || 5} VGTs · ${form.gaming_agreement || "Revenue Share"}`]] : []),
                ] as [string, string | undefined][])
                  .filter(([, v]) => v)
                  .map(([label, val], i, arr) => (
                    <div
                      key={i}
                      className={`flex justify-between px-4 py-2.5 ${
                        i < arr.length - 1 ? "border-b border-[var(--border-light)]" : ""
                      }`}
                    >
                      <span className="text-[13px] text-[var(--text-muted)]">{label}</span>
                      <span className="text-[13px] font-semibold">{val}</span>
                    </div>
                  ))}
              </div>

              <div className="mt-4 p-3.5 rounded-xl bg-[var(--accent)]/10 border border-[var(--accent)]/20">
                <div className="text-xs text-[var(--accent)] font-semibold mb-1">Pipeline will execute:</div>
                <div className="text-xs text-[var(--text-muted)] leading-relaxed">
                  Intake → Feasibility → Market → Cost → Finance → Risk → Decision
                  {form.include_gaming && " + EGM Forecast + Contract Simulation"}
                  {" + Portfolio Impact Analysis"}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3.5 border-t border-[var(--border)] flex justify-between">
          <button
            onClick={() => (step > 0 ? setStep(step - 1) : onClose())}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[var(--border)] text-[13px] text-[var(--text-muted)] hover:bg-[var(--hover)] transition-colors"
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
              className="flex items-center gap-2 px-6 py-2 rounded-lg text-[13px] font-bold bg-[var(--green)] text-white hover:opacity-90 transition-all"
            >
              <Zap size={14} /> Run Pipeline
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
