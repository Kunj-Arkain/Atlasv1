"use client";

import type { FieldDef } from "@/lib/constants";

interface FieldGroupProps {
  fields: FieldDef[];
  values: Record<string, string | boolean | undefined>;
  onChange: (key: string, value: string) => void;
}

export function FieldGroup({ fields, values, onChange }: FieldGroupProps) {
  return (
    <div className={`grid gap-3.5 ${fields.length > 3 ? "grid-cols-2" : "grid-cols-1"}`}>
      {fields.map((f) => (
        <div key={f.key}>
          <label className="text-xs text-[var(--text-muted)] font-semibold block mb-1">
            {f.label}
            {f.required && <span className="text-[var(--red)] ml-0.5">*</span>}
          </label>
          {f.type === "select" ? (
            <select
              value={String(values[f.key] || "")}
              onChange={(e) => onChange(f.key, e.target.value)}
              className="w-full bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-sm text-[var(--text)] font-sans outline-none appearance-none cursor-pointer"
            >
              <option value="">Select...</option>
              {f.options?.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              inputMode={f.type === "number" ? "numeric" : "text"}
              value={String(values[f.key] || "")}
              onChange={(e) => onChange(f.key, e.target.value)}
              placeholder={f.placeholder}
              className="w-full bg-[var(--hover)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-sm text-[var(--text)] font-sans outline-none placeholder:text-[var(--text-dim)]"
            />
          )}
        </div>
      ))}
    </div>
  );
}
