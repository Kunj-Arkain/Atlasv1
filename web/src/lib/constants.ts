import {
  Building2, Fuel, Coffee, DollarSign, Store, Package,
  type LucideIcon,
} from "lucide-react";

// ═══════════════════════════════════════════════════════════════
// Property type configuration
// ═══════════════════════════════════════════════════════════════

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  placeholder?: string;
  options?: string[];
  required?: boolean;
}

export interface PropertyTypeConfig {
  label: string;
  icon: LucideIcon;
  color: string;
  description: string;
  fields: FieldDef[];
  gamingEligible: boolean;
}

export const PROPERTY_TYPES: Record<string, PropertyTypeConfig> = {
  retail_strip: {
    label: "Retail Strip Center",
    icon: Building2,
    color: "#6366f1",
    description: "Multi-tenant retail with steady lease income",
    fields: [
      { key: "num_units", label: "Number of Units", type: "number", placeholder: "e.g. 6" },
      { key: "sqft", label: "Total Sq Ft", type: "number", placeholder: "e.g. 12000" },
      { key: "occupancy_rate", label: "Occupancy Rate (%)", type: "number", placeholder: "e.g. 92" },
      { key: "avg_lease_rate", label: "Avg Lease Rate ($/sqft/yr)", type: "number", placeholder: "e.g. 14.50" },
    ],
    gamingEligible: true,
  },
  gas_station: {
    label: "Gas Station / C-Store",
    icon: Fuel,
    color: "#f97316",
    description: "Fuel + convenience with high gaming upside in IL",
    fields: [
      { key: "fuel_gallons_monthly", label: "Monthly Fuel Volume (gal)", type: "number", placeholder: "e.g. 120000" },
      { key: "cstore_revenue_monthly", label: "C-Store Monthly Revenue", type: "number", placeholder: "e.g. 45000" },
      { key: "lot_size_acres", label: "Lot Size (acres)", type: "number", placeholder: "e.g. 0.75" },
    ],
    gamingEligible: true,
  },
  qsr: {
    label: "QSR / Fast Food",
    icon: Coffee,
    color: "#22c55e",
    description: "Quick-service restaurant with drive-through potential",
    fields: [
      { key: "brand", label: "Brand / Concept", type: "text", placeholder: "e.g. Subway, Independent" },
      { key: "drive_through", label: "Drive-Through", type: "select", options: ["Yes", "No"] },
      { key: "sqft", label: "Building Sq Ft", type: "number", placeholder: "e.g. 2800" },
      { key: "monthly_revenue", label: "Monthly Revenue", type: "number", placeholder: "e.g. 85000" },
    ],
    gamingEligible: true,
  },
  dollar: {
    label: "Dollar Store",
    icon: DollarSign,
    color: "#eab308",
    description: "Net-lease retail with predictable tenant income",
    fields: [
      { key: "tenant_name", label: "Tenant Name", type: "text", placeholder: "e.g. Dollar General" },
      { key: "lease_years_remaining", label: "Lease Years Remaining", type: "number", placeholder: "e.g. 8" },
      { key: "sqft", label: "Building Sq Ft", type: "number", placeholder: "e.g. 9100" },
      { key: "nnn_lease", label: "Lease Type", type: "select", options: ["NNN", "Modified Gross", "Gross"] },
    ],
    gamingEligible: true,
  },
  bin_store: {
    label: "Bin Store",
    icon: Package,
    color: "#14b8a6",
    description: "Liquidation / overstock retail with high foot traffic",
    fields: [
      { key: "sqft", label: "Store Sq Ft", type: "number", placeholder: "e.g. 8000" },
      { key: "bin_count", label: "Number of Bins", type: "number", placeholder: "e.g. 40" },
      { key: "restock_schedule", label: "Restock Frequency", type: "select", options: ["Daily", "2x/week", "Weekly"] },
      { key: "avg_daily_revenue", label: "Avg Daily Revenue ($)", type: "number", placeholder: "e.g. 3500" },
      { key: "supplier", label: "Supplier / Source", type: "text", placeholder: "e.g. Amazon returns, Target overstock" },
    ],
    gamingEligible: true,
  },
  shopping_center: {
    label: "Shopping Center",
    icon: Store,
    color: "#ec4899",
    description: "Larger multi-tenant retail with anchor tenants",
    fields: [
      { key: "sqft", label: "Total GLA (Sq Ft)", type: "number", placeholder: "e.g. 65000" },
      { key: "num_units", label: "Number of Spaces", type: "number", placeholder: "e.g. 12" },
      { key: "anchor_tenant", label: "Anchor Tenant", type: "text", placeholder: "e.g. Dollar Tree" },
      { key: "occupancy_rate", label: "Occupancy Rate (%)", type: "number", placeholder: "e.g. 88" },
      { key: "year_built", label: "Year Built", type: "number", placeholder: "e.g. 1998" },
    ],
    gamingEligible: true,
  },
};

export const STATES = ["IL", "NV", "PA", "CO", "IN", "OH", "WV"];

export const GAMING_FIELDS: FieldDef[] = [
  { key: "terminal_count", label: "Number of VGTs", type: "number", placeholder: "1–6 (IL max is 6)", required: true },
  { key: "gaming_agreement", label: "Agreement Type", type: "select", options: ["Revenue Share", "Flat Lease", "Hybrid"] },
  { key: "operator_split", label: "Operator Split (%)", type: "number", placeholder: "e.g. 65" },
  { key: "gaming_operator", label: "Terminal Operator", type: "text", placeholder: "e.g. J&J Ventures, Gold Rush" },
];

export const FINANCING_FIELDS: FieldDef[] = [
  { key: "down_payment_pct", label: "Down Payment (%)", type: "number", placeholder: "e.g. 25" },
  { key: "loan_rate", label: "Interest Rate (%)", type: "number", placeholder: "e.g. 6.75" },
  { key: "loan_term_years", label: "Loan Term (years)", type: "number", placeholder: "e.g. 25" },
  { key: "lender", label: "Lender", type: "text", placeholder: "e.g. First Midwest Bank" },
];

export const CORE_FIELDS: FieldDef[] = [
  { key: "deal_name", label: "Deal Name", type: "text", placeholder: "e.g. Springfield Gas Station" },
  { key: "address", label: "Address", type: "text", placeholder: "123 Main St, Springfield, IL", required: true },
  { key: "state", label: "State", type: "select", options: STATES, required: true },
  { key: "municipality", label: "Municipality", type: "text", placeholder: "e.g. Springfield" },
  { key: "purchase_price", label: "Purchase Price ($)", type: "number", placeholder: "e.g. 1500000", required: true },
  { key: "noi", label: "Annual NOI ($)", type: "number", placeholder: "e.g. 120000" },
  { key: "year_built", label: "Year Built", type: "number", placeholder: "e.g. 2005" },
  { key: "lot_size", label: "Lot Size (acres)", type: "number", placeholder: "e.g. 0.5" },
];
