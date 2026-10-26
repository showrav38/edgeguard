// Plain-language text for the Operator view: what a technician or a visitor reads in 5 seconds.
// Pure functions (no React), unit-tested in plain.test.ts.
import type { AlarmEvent, LatchedAlarm } from "./stream";
import type { Telemetry } from "./types";

export type Light = "green" | "amber" | "red" | "off";

// Failure types in everyday words.
export const PLAIN_FAILURE: Record<string, string> = {
  tool_wear: "worn-out tool",
  heat_dissipation: "overheating (poor cooling)",
  power: "power problem",
  overstrain: "overload",
  none: "no problem",
};

// One imperative per ActionCode (edge_app/actions.py). The engineer view shows the full edge text.
export const PLAIN_ACTION: Record<number, string> = {
  0: "Nothing to do. Keep running.",
  1: "Replace the tool before the next batch.",
  2: "Check the cooling and the fans.",
  3: "Check the load and the drive settings.",
  4: "Reduce the load or change the tool.",
  5: "Keep an eye on the machine and plan a check at the next stop.",
};

// Tools in the AI4I data fail between 200 and 240 min of use; 90 % of training rows are below 197 min.
export const TOOL_LIMIT_MIN = 200;

// "Usual" range = 10th to 90th percentile of the training data (models/model_info.json -> training_stats).
const USUAL: Record<string, { name: string; lo: number; hi: number; unit: string; digits: number; celsius?: boolean }> = {
  AirTempK: { name: "Air temperature", lo: 297.4, hi: 303.4, unit: "°C", digits: 0, celsius: true },
  ProcessTempK: { name: "Process temperature", lo: 307.8, hi: 312.3, unit: "°C", digits: 0, celsius: true },
  SpeedRpm: { name: "Spindle speed", lo: 1364, hi: 1748, unit: "rpm", digits: 0 },
  TorqueNm: { name: "Torque", lo: 27.2, hi: 52.5, unit: "Nm", digits: 0 },
};

const show = (v: number, u: (typeof USUAL)[string]) =>
  `${(u.celsius ? v - 273.15 : v).toFixed(u.digits)} ${u.unit}`;

/** Everyday name of a signal ("Air temperature"), used for drift notices. */
export function plainSignal(signal: string): string {
  if (signal === "ToolWearMin") return "tool use time";
  return USUAL[signal]?.name.toLowerCase() ?? signal;
}

/** One sentence about one signal, e.g. "Tool used 212 min, normal limit about 200 min." */
export function describeSignal(signal: string, value: number): string {
  if (signal === "ToolWearMin") {
    const v = Math.round(value);
    return v >= TOOL_LIMIT_MIN
      ? `Tool used ${v} min, normal limit about ${TOOL_LIMIT_MIN} min.`
      : `Tool used ${v} min, getting close to the normal limit of about ${TOOL_LIMIT_MIN} min.`;
  }
  const u = USUAL[signal];
  if (!u) return `${signal} is unusual.`;
  const side = value > u.hi ? "higher than usual" : value < u.lo ? "lower than usual" : "unusual for this situation";
  return `${u.name} ${show(value, u)}, ${side} (normally ${show(u.lo, u)} to ${show(u.hi, u)}).`;
}

/** The reading whose action and reasons are shown: the latched alarm's worst reading, else the latest. */
export function shownReading(latest: Telemetry, latched: LatchedAlarm | null): Telemetry {
  return latched ? latched.peak : latest;
}

/** The main reason in one sentence, from the explanation's top factor (null when nothing pushes the risk). */
export function mainReason(m: Telemetry): string | null {
  if (m.state === "OK") return null;
  const top = m.top_factors.find((f) => f.effect >= 0.005); // at least 0.5 risk points
  if (!top) return null;
  const value = (m.signals as unknown as Record<string, number>)[top.signal];
  return describeSignal(top.signal, value);
}

export interface Headline {
  light: Light;
  word: string; // short label next to the light
  sentence: string; // the one sentence to read
}

/** Traffic light + sentence. A latched alarm wins over the latest reading (it stays until acknowledged). */
export function headline(latest: Telemetry | null, latched: LatchedAlarm | null, live: boolean): Headline {
  if (!live) return { light: "off", word: "No data", sentence: "No live data from Machine 1 right now." };
  if (!latest) return { light: "off", word: "Waiting", sentence: "Waiting for the first reading from Machine 1…" };
  if (latched) {
    const what = PLAIN_FAILURE[latched.peak.failure_type] ?? latched.peak.failure_type;
    if (!latched.active) {
      return { light: "red", word: "Act now",
               sentence: `There was an alarm (${what}). Check the machine, then press Acknowledge.` };
    }
    return latched.acknowledged
      ? { light: "red", word: "Act now", sentence: `Alarm acknowledged: ${what} is still likely. Fix it now.` }
      : { light: "red", word: "Act now", sentence: `Machine 1 is likely to fail soon: ${what}.` };
  }
  if (latest.state === "WARNING") {
    return { light: "amber", word: "Attention soon",
             sentence: `The risk of a failure is rising (${PLAIN_FAILURE[latest.failure_type] ?? latest.failure_type}).` };
  }
  if (latest.drift_warning) {
    return { light: "amber", word: "Attention",
             sentence: "Machine 1 runs in unusual conditions, so the prediction is less reliable." };
  }
  return { light: "green", word: "Normal", sentence: "Machine 1 is running normally." };
}

const clock = (ts: string) => new Date(ts).toLocaleTimeString([], { hour12: false });

/** One alarm-history row as a short sentence. */
export function alarmSentence(a: AlarmEvent, duration: string | null): string {
  const what = PLAIN_FAILURE[a.failureType] ?? a.failureType;
  const lasted = duration ? `lasted ${duration}` : "still active";
  const truth = a.actualFailure ? "a real failure happened" : "no failure happened (false alarm)";
  return `${clock(a.ts)}: alarm for ${what} (risk ${Math.round(a.risk * 100)} %), ${lasted}. Replay check: ${truth}.`;
}
