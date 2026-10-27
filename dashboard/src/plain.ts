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

// The failure rules of the AI4I data set (same numbers as training/rules_check.py).
const HDF_MAX_TEMP_DIFF_K = 8.6;
const HDF_MAX_SPEED_RPM = 1380;
const PWF_MIN_W = 3500;
const PWF_MAX_W = 9000;
const OSF_LIMIT: Record<string, number> = { L: 11000, M: 12000, H: 13000 }; // tool wear x torque, min*Nm

const powerW = (s: Telemetry["signals"]) => s.TorqueNm * s.SpeedRpm * (2 * Math.PI / 60);
const kw = (w: number) => `${(w / 1000).toFixed(2)} kW`; // 2 decimals: 9.01 kW must not read as "9.0 kW"
const limitKw = (w: number) => `${w / 1000} kW`;
const n0 = (v: number) => Math.round(v).toLocaleString("en-US");

/**
 * The reason for the predicted failure type, in that type's own physics. A power alarm gets a power reason,
 * an overload alarm a tool wear x torque reason, and so on. Without this, the top explanation factor
 * (e.g. process temperature) could be named as the reason for a power alarm, which reads as nonsense.
 * The model's own top 2 signals stay in the Engineer view.
 */
export function typeReason(m: Telemetry): string | null {
  const s = m.signals;
  switch (m.failure_type) {
    case "tool_wear":
      return describeSignal("ToolWearMin", s.ToolWearMin);
    case "power": {
      const w = powerW(s);
      const side = w > PWF_MAX_W ? `above the usual maximum of ${limitKw(PWF_MAX_W)}`
        : w < PWF_MIN_W ? `below the usual minimum of ${limitKw(PWF_MIN_W)}`
        : w > (PWF_MIN_W + PWF_MAX_W) / 2 ? `close to the ${limitKw(PWF_MAX_W)} maximum` : `close to the ${limitKw(PWF_MIN_W)} minimum`;
      return `Power ${kw(w)} (${n0(s.TorqueNm)} Nm at ${n0(s.SpeedRpm)} rpm), ${side}.`;
    }
    case "heat_dissipation": {
      const diff = s.ProcessTempK - s.AirTempK;
      const weak = diff < HDF_MAX_TEMP_DIFF_K && s.SpeedRpm < HDF_MAX_SPEED_RPM;
      return `Process only ${diff.toFixed(1)} °C warmer than the air at ${n0(s.SpeedRpm)} rpm: `
        + (weak ? "too little to carry the heat away" : "close to the point where the heat is not carried away")
        + ` (needs over ${HDF_MAX_TEMP_DIFF_K} °C or over ${n0(HDF_MAX_SPEED_RPM)} rpm).`;
    }
    case "overstrain": {
      const load = s.ToolWearMin * s.TorqueNm;
      const limit = OSF_LIMIT[s.ProductType] ?? OSF_LIMIT.L;
      return `Load ${n0(load)} min·Nm (tool use × torque), ${load > limit ? "above" : "close to"} the limit of about `
        + `${n0(limit)} for product ${s.ProductType}.`;
    }
    default:
      return null;
  }
}

/** The main reason in one sentence: the predicted failure type's reason, else the explanation's top factor. */
export function mainReason(m: Telemetry): string | null {
  if (m.state === "OK") return null;
  const byType = typeReason(m);
  if (byType) return byType;
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

const clock = (ts: string | number) => new Date(ts).toLocaleTimeString([], { hour12: false });
const capital = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/**
 * Traffic light + sentence. A latched alarm wins over the latest reading (it stays until acknowledged).
 * Red only while the risk is above the threshold (an active alarm); an alarm that is over but not yet
 * acknowledged, or a repeat during the cool-down after Acknowledge, is amber.
 * `alarms` (newest first) and `nowMs` are only needed for the repeat sentence.
 */
export function headline(latest: Telemetry | null, latched: LatchedAlarm | null, live: boolean,
                         alarms: AlarmEvent[] = [], nowMs = Date.now()): Headline {
  if (!live) return { light: "off", word: "No data", sentence: "No live data from Machine 1 right now." };
  if (!latest) return { light: "off", word: "Waiting", sentence: "Waiting for the first reading from Machine 1…" };
  if (latched) {
    const what = PLAIN_FAILURE[latched.peak.failure_type] ?? latched.peak.failure_type;
    if (!latched.active) {
      return { light: "amber", word: "Check needed",
               sentence: `Alarm at ${clock(latched.startTs)} needs your check (${what}).` };
    }
    return latched.acknowledged
      ? { light: "red", word: "Act now", sentence: `Alarm acknowledged: ${what} is still likely. Fix it now.` }
      : { light: "red", word: "Act now", sentence: `Machine 1 is likely to fail soon: ${what}.` };
  }
  if (latest.state === "ALARM") {
    // ALARM without a latch = a repeat of a type acknowledged a moment ago (cool-down).
    const g = groupAlarms(alarms)[0];
    const what = PLAIN_FAILURE[latest.failure_type] ?? latest.failure_type;
    return { light: "amber", word: "Attention",
             sentence: g && g.failureType === latest.failure_type && g.events.length > 1
               ? groupHeadline(g, nowMs) : `${capital(what)} again, already acknowledged.` };
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

/** One alarm-history row as a short sentence. */
export function alarmSentence(a: AlarmEvent, duration: string | null): string {
  const what = PLAIN_FAILURE[a.failureType] ?? a.failureType;
  const lasted = duration ? `lasted ${duration}` : "still active";
  const truth = a.actualFailure ? "a real failure happened" : "no failure happened (false alarm)";
  return `${clock(a.ts)}: alarm for ${what} (risk ${Math.round(a.risk * 100)} %), ${lasted}. Replay check: ${truth}.`;
}

// ---------- alarm flood: repeated alarms of one type become one line ----------
export const GROUP_GAP_MS = 5 * 60_000; // alarms of the same type less than 5 min apart belong to one group

export interface AlarmGroup {
  failureType: string;
  events: AlarmEvent[]; // newest first; every alarm is kept, nothing is dropped
}

/** Groups alarms (newest first) by failure type; a gap over GROUP_GAP_MS starts a new group. Newest group first. */
export function groupAlarms(alarms: AlarmEvent[], gapMs = GROUP_GAP_MS): AlarmGroup[] {
  const groups: AlarmGroup[] = [];
  const open = new Map<string, AlarmGroup>(); // failure type -> its group still collecting older alarms
  for (const a of alarms) {
    const g = open.get(a.failureType);
    const oldest = g?.events[g.events.length - 1];
    if (g && oldest && Date.parse(oldest.ts) - Date.parse(a.ts) <= gapMs) {
      g.events.push(a);
    } else {
      const fresh = { failureType: a.failureType, events: [a] };
      groups.push(fresh);
      open.set(a.failureType, fresh);
    }
  }
  return groups;
}

/** "Power problem: 4 alarms in the last 3 min" (minutes rounded up, at least 1). */
export function groupHeadline(g: AlarmGroup, nowMs: number): string {
  const oldest = Date.parse(g.events[g.events.length - 1].ts);
  const min = Math.max(1, Math.ceil((nowMs - oldest) / 60_000));
  return `${capital(PLAIN_FAILURE[g.failureType] ?? g.failureType)}: ${g.events.length} alarms in the last ${min} min`;
}

/** One history line for a group: a single alarm keeps its sentence, a repeat is counted. */
export function groupSentence(g: AlarmGroup, nowMs: number, duration: string | null): string {
  if (g.events.length === 1) return alarmSentence(g.events[0], duration);
  const real = g.events.filter((a) => a.actualFailure).length;
  const newest = g.events[0], oldest = g.events[g.events.length - 1];
  const recent = nowMs - Date.parse(newest.ts) < GROUP_GAP_MS;
  const head = recent ? groupHeadline(g, nowMs)
    : `${capital(PLAIN_FAILURE[g.failureType] ?? g.failureType)}: ${g.events.length} alarms from ${clock(oldest.ts)} to ${clock(newest.ts)}`;
  const peak = Math.max(...g.events.map((a) => a.risk));
  return `${head} (highest risk ${Math.round(peak * 100)} %). Replay check: ${real} real, ${g.events.length - real} false.`;
}
