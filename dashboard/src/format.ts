// Display names, units and number formatting shared by the components.

export const SIGNALS: Record<string, { label: string; unit: string; digits: number }> = {
  AirTempK: { label: "Air temperature", unit: "K", digits: 1 },
  ProcessTempK: { label: "Process temperature", unit: "K", digits: 1 },
  SpeedRpm: { label: "Rotational speed", unit: "rpm", digits: 0 },
  TorqueNm: { label: "Torque", unit: "Nm", digits: 1 },
  ToolWearMin: { label: "Tool wear", unit: "min", digits: 0 },
  ProductType: { label: "Product type", unit: "", digits: 0 },
};

export const FAILURE_TYPES: Record<string, string> = {
  tool_wear: "Tool wear",
  heat_dissipation: "Heat dissipation",
  power: "Power",
  overstrain: "Overstrain",
  none: "—",
};

export const ms = (v: number | null | undefined) => (v == null ? "–" : `${v < 10 ? v.toFixed(1) : v.toFixed(0)} ms`);
export const pct = (v: number) => `${(v * 100).toFixed(0)} %`;
export const int = (v: number) => v.toLocaleString("en-US");

export function signalValue(name: string, value: number | string): string {
  const s = SIGNALS[name];
  if (!s || typeof value === "string") return String(value);
  return `${value.toFixed(s.digits)} ${s.unit}`;
}
