// Pure logic for the telemetry stream: duplicate filter, chart history, alarm history.
// No React and no MQTT here, so it is easy to unit-test (stream.test.ts).
import type { Telemetry } from "./types";

export const HISTORY_POINTS = 100; // points per live chart
export const ALARM_EVENTS = 20; // rows in the alarm history

/**
 * Drops duplicate telemetry by seq.
 *
 * The edge app delivers "at least once" (MQTT QoS 1 + SQLite outbox): after a reconnect the same
 * message can arrive twice. A re-sent message is byte-identical, so it has the same seq AND the
 * same ts. Same seq with a DIFFERENT ts is not a duplicate: the PLC simulator restarted and counts
 * from 1 again, so we accept it. We remember the last `capacity` seqs only (bounded memory).
 */
export class SeqDeduper {
  private seen = new Map<number, string>(); // seq -> ts
  private order: number[] = [];
  dropped = 0;

  constructor(private capacity = 5000) {}

  accept(seq: number, ts: string): boolean {
    const prevTs = this.seen.get(seq);
    if (prevTs === ts) {
      this.dropped += 1;
      return false;
    }
    if (prevTs === undefined) {
      this.order.push(seq);
      if (this.order.length > this.capacity) this.seen.delete(this.order.shift()!);
    }
    this.seen.set(seq, ts);
    return true;
  }
}

export interface Point {
  seq: number;
  time: string; // HH:MM:SS, x-axis label
  risk: number;
  ToolWearMin: number;
  TorqueNm: number;
  AirTempK: number;
  ProcessTempK: number;
  SpeedRpm: number;
}

export interface AlarmEvent {
  seq: number;
  ts: string;
  endTs: string | null; // first reading after the alarm (null while it is still in ALARM)
  failureType: string;
  risk: number;
  action: string;
  actualFailure: boolean;
  actualType: string;
  buffered: boolean;
}

/**
 * A latched alarm stays on screen until the operator acknowledges it, even if the readings go back
 * to OK (a short alarm must not disappear before anyone saw it).
 *   - starts with the first ALARM reading
 *   - active = the latest reading is still ALARM; endTs = when it left ALARM
 *   - Acknowledge while active -> acknowledged, removed when the readings leave ALARM
 *   - Acknowledge after it cleared -> removed at once
 */
export interface LatchedAlarm {
  startSeq: number;
  startTs: string;
  peak: Telemetry; // the reading with the highest risk: its action and factors are shown
  active: boolean;
  endTs: string | null;
  acknowledged: boolean;
}

export interface View {
  latest: Telemetry | null;
  points: Point[];
  alarms: AlarmEvent[]; // newest first
  latched: LatchedAlarm | null;
  liveAgeMs: number | null; // edge app -> browser, last non-buffered message
}

export const emptyView: View = { latest: null, points: [], alarms: [], latched: null, liveAgeMs: null };

function updateLatch(latched: LatchedAlarm | null, m: Telemetry): LatchedAlarm | null {
  if (m.state === "ALARM") {
    if (!latched) {
      return { startSeq: m.seq, startTs: m.ts, peak: m, active: true, endTs: null, acknowledged: false };
    }
    // Same latched alarm (it may have cleared and come back before anyone acknowledged it).
    return { ...latched, active: true, endTs: null, peak: m.risk > latched.peak.risk ? m : latched.peak };
  }
  if (!latched || !latched.active) return latched;
  // The condition just cleared: an acknowledged alarm goes away, an unacknowledged one stays.
  return latched.acknowledged ? null : { ...latched, active: false, endTs: m.ts };
}

export function acknowledge(view: View): View {
  const l = view.latched;
  if (!l) return view;
  return { ...view, latched: l.active ? { ...l, acknowledged: true } : null };
}

/** "45 s", "3 min 12 s", "1 h 05 min" */
export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (ms < 1000) return "<1 s";
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

export function addTelemetry(view: View, m: Telemetry, nowMs: number): View {
  const s = m.signals;
  const point: Point = {
    seq: m.seq,
    time: new Date(m.ts).toLocaleTimeString([], { hour12: false }),
    risk: m.risk,
    ToolWearMin: s.ToolWearMin,
    TorqueNm: s.TorqueNm,
    AirTempK: s.AirTempK,
    ProcessTempK: s.ProcessTempK,
    SpeedRpm: s.SpeedRpm,
  };
  const points = [...view.points, point].slice(-HISTORY_POINTS);

  // A new alarm EVENT is the step into ALARM, not every reading while it stays in ALARM.
  let alarms = view.alarms;
  if (m.state === "ALARM" && view.latest?.state !== "ALARM") {
    const event: AlarmEvent = {
      seq: m.seq,
      ts: m.ts,
      endTs: null,
      failureType: m.failure_type,
      risk: m.risk,
      action: m.action.text,
      actualFailure: m.ground_truth.failure,
      actualType: m.ground_truth.type,
      buffered: m.buffered,
    };
    alarms = [event, ...alarms].slice(0, ALARM_EVENTS);
  } else if (m.state !== "ALARM" && alarms[0] && alarms[0].endTs === null) {
    alarms = [{ ...alarms[0], endTs: m.ts }, ...alarms.slice(1)];
  }

  // Buffered messages are late on purpose; their age says nothing about the live latency.
  const liveAgeMs = m.buffered ? view.liveAgeMs : Math.max(0, nowMs - Date.parse(m.ts));
  return { latest: m, points, alarms, latched: updateLatch(view.latched, m), liveAgeMs };
}
