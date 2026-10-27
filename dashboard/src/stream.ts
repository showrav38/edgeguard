// Pure logic for the telemetry stream: duplicate filter, chart history, alarm history.
// No React and no MQTT here, so it is easy to unit-test (stream.test.ts).
// The alarm state (latch, acknowledge, cool-down, counters) is NOT decided here: the edge app owns it and
// publishes it on edgeguard/machine1/alarm_state (edge_app/alarms.py, D-32); this file only displays it.
import type { AlarmState, Telemetry } from "./types";

export const HISTORY_POINTS = 100; // points per live chart
export const ALARM_EVENTS = 200; // rows in the alarm history (the counters below count every alarm)
// Alarm rate window. ISA-18.2 calls more than 10 alarms in 10 min per operator an alarm flood.
export const RATE_WINDOW_MS = 10 * 60_000;
export const FLOOD_PER_WINDOW = 10;

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
  alarm: boolean; // the reading was in ALARM: drawn as a red marker on the charts
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
  suppressed: boolean; // the edge app did not latch it: cool-down after an Acknowledge of the same type
}

/**
 * The latched alarm shown on screen, taken from the edge app's alarm_state (one failure type; if several
 * are latched, the one that most needs the operator: see shownLatch). It stays until acknowledged, even if
 * the readings go back to OK. active = its condition is still there; endTs = when it left ALARM.
 */
export interface LatchedAlarm {
  type: string; // failure type: what Acknowledge sends
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
  alarmState: AlarmState | null; // latest edgeguard/machine1/alarm_state
  latched: LatchedAlarm | null; // from alarmState
  liveAgeMs: number | null; // edge app -> browser, last non-buffered message
  missing: number; // readings never received: gaps in seq (should stay 0 through an outage)
  bufferedReceived: number; // readings delivered late from the edge app's buffer
  // From alarmState (the edge app counts every alarm since it started, also during a cool-down):
  cooldownUntil: Record<string, number>; // failure type -> end of its cool-down (ms, edge app clock)
  alarmTotal: number; // every alarm, also those no longer in `alarms`
  alarmFalse: number; // ... of which no real failure happened (replay ground truth)
  recentAlarms: number; // alarms within the last RATE_WINDOW_MS
  watchedMs: number; // how long the edge app has counted (up to RATE_WINDOW_MS)
}

export const emptyView: View = {
  latest: null, points: [], alarms: [], alarmState: null, latched: null, liveAgeMs: null, missing: 0,
  bufferedReceived: 0, cooldownUntil: {}, alarmTotal: 0, alarmFalse: 0, recentAlarms: 0, watchedMs: 0,
};

/**
 * Which latched failure type the screen shows (it has room for one): first one that needs an Acknowledge
 * and is still active, then one that needs an Acknowledge (oldest first), then an acknowledged active one.
 * After that one is acknowledged, the next one is shown, so no latched alarm is acknowledged unseen.
 */
export function shownLatch(s: AlarmState): LatchedAlarm | null {
  const latched = Object.entries(s.types)
    .filter(([, t]) => (t.state === "ACTIVE" || t.state === "WAITING_FOR_ACK") && t.peak && t.start_ts)
    .map(([type, t]): LatchedAlarm => ({
      type, startSeq: t.peak!.seq, startTs: t.start_ts!, peak: t.peak!, active: t.state === "ACTIVE",
      endTs: t.end_ts, acknowledged: t.acknowledged,
    }));
  const rank = (l: LatchedAlarm) => (l.acknowledged ? 2 : l.active ? 0 : 1);
  latched.sort((a, b) => rank(a) - rank(b) || Date.parse(a.startTs) - Date.parse(b.startTs));
  return latched[0] ?? null;
}

/** Shows a new alarm_state from the edge app (the dashboard decides nothing about latching itself). */
export function applyAlarmState(view: View, s: AlarmState): View {
  const cooldownUntil: Record<string, number> = {};
  for (const [type, t] of Object.entries(s.types)) {
    if (t.state === "COOLDOWN" && t.cooldown_until) cooldownUntil[type] = Date.parse(t.cooldown_until);
  }
  return {
    ...view, alarmState: s, latched: shownLatch(s), cooldownUntil,
    alarmTotal: s.counters.total, alarmFalse: s.counters.false,
    recentAlarms: s.counters.last_10min, watchedMs: s.counters.watched_s * 1000,
  };
}

/** Alarms per hour over the last RATE_WINDOW_MS (or over the time watched, if shorter). null under 1 min. */
export function alarmRatePerHour(view: View): number | null {
  if (view.watchedMs < 60_000) return null;
  return (view.recentAlarms / view.watchedMs) * 3_600_000;
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
    alarm: m.state === "ALARM",
  };
  const points = [...view.points, point].slice(-HISTORY_POINTS);

  // A new alarm EVENT (history row) is the step into ALARM, not every reading while it stays in ALARM.
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
      suppressed: !!m.alarm_suppressed, // decided by the edge app
    };
    alarms = [event, ...alarms].slice(0, ALARM_EVENTS);
  } else if (m.state !== "ALARM" && alarms[0] && alarms[0].endTs === null) {
    alarms = [{ ...alarms[0], endTs: m.ts }, ...alarms.slice(1)];
  }

  // Buffered messages are late on purpose; their age says nothing about the live latency.
  const liveAgeMs = m.buffered ? view.liveAgeMs : Math.max(0, nowMs - Date.parse(m.ts));
  // A jump forward in seq = readings that never arrived. A jump backwards is a PLC restart, not a loss.
  const prevSeq = view.latest?.seq;
  const gap = prevSeq != null && m.seq > prevSeq + 1 ? m.seq - prevSeq - 1 : 0;
  return {
    ...view, latest: m, points, alarms, liveAgeMs,
    missing: view.missing + gap,
    bufferedReceived: view.bufferedReceived + (m.buffered ? 1 : 0),
  };
}
