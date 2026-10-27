import { describe, expect, it } from "vitest";
import {
  addTelemetry, alarmRatePerHour, applyAlarmState, emptyView, formatDuration, HISTORY_POINTS, SeqDeduper,
} from "./stream";
import type { AlarmState, AlarmTypeInfo, Telemetry } from "./types";

function msg(seq: number, state: Telemetry["state"] = "OK", ts = `2026-10-26T10:00:00.${seq}Z`): Telemetry {
  return {
    seq, ts, state, risk: state === "ALARM" ? 0.8 : 0.01, alarm: state === "ALARM",
    signals: { AirTempK: 300, ProcessTempK: 310, SpeedRpm: 1500, TorqueNm: 40, ToolWearMin: seq, ProductType: "L" },
    failure_type: state === "ALARM" ? "tool_wear" : "none", action: { code: 0, text: "" },
    top_factors: [], drift_score: null, drift_warning: false, drift_signals: [],
    inference_ms: 1, plc_io_ms: 5, buffered: false, buffer_backlog: 0, buffer_dropped: 0,
    ground_truth: { failure: false, type: "none" }, missed_cycles: 0,
  };
}

describe("SeqDeduper", () => {
  it("drops a re-delivered message (same seq, same ts) and counts it", () => {
    const d = new SeqDeduper();
    expect(d.accept(1, "a")).toBe(true);
    expect(d.accept(2, "b")).toBe(true);
    expect(d.accept(2, "b")).toBe(false);
    expect(d.accept(1, "a")).toBe(false);
    expect(d.dropped).toBe(2);
  });

  it("accepts the same seq with a new ts (PLC restarted and counts again)", () => {
    const d = new SeqDeduper();
    expect(d.accept(1, "run1")).toBe(true);
    expect(d.accept(1, "run2")).toBe(true);
    expect(d.dropped).toBe(0);
  });

  it("forgets the oldest seqs beyond its capacity", () => {
    const d = new SeqDeduper(3);
    [1, 2, 3, 4].forEach((s) => d.accept(s, "t"));
    expect(d.accept(1, "t")).toBe(true); // forgotten -> accepted
    expect(d.accept(4, "t")).toBe(false); // still remembered
  });
});

describe("addTelemetry", () => {
  it(`keeps the last ${HISTORY_POINTS} points`, () => {
    let v = emptyView;
    for (let i = 1; i <= 150; i++) v = addTelemetry(v, msg(i), 0);
    expect(v.points).toHaveLength(HISTORY_POINTS);
    expect(v.points[0].seq).toBe(51);
    expect(v.latest?.seq).toBe(150);
  });

  it("records one alarm event per step into ALARM", () => {
    let v = emptyView;
    for (const [seq, st] of [[1, "OK"], [2, "ALARM"], [3, "ALARM"], [4, "OK"], [5, "ALARM"]] as const) {
      v = addTelemetry(v, msg(seq, st), 0);
    }
    expect(v.alarms.map((a) => a.seq)).toEqual([5, 2]);
    expect(v.alarms[1].endTs).toBe(msg(4).ts); // the first alarm ended at seq 4
    expect(v.alarms[0].endTs).toBeNull(); // the second is still active
  });

  it("measures live latency only on non-buffered messages", () => {
    const ts = "2026-10-26T10:00:00.000Z";
    let v = addTelemetry(emptyView, msg(1, "OK", ts), Date.parse(ts) + 30);
    expect(v.liveAgeMs).toBe(30);
    v = addTelemetry(v, { ...msg(2, "OK", ts), buffered: true }, Date.parse(ts) + 20000);
    expect(v.liveAgeMs).toBe(30);
  });
});

describe("delivery counters", () => {
  it("counts seq gaps as missing, but not a restart (seq going back)", () => {
    let v = emptyView;
    for (const seq of [1, 2, 5, 6, 1, 2]) v = addTelemetry(v, msg(seq), 0);
    expect(v.missing).toBe(2); // 3 and 4
  });

  it("counts readings delivered from the buffer", () => {
    let v = addTelemetry(emptyView, msg(1), 0);
    v = addTelemetry(v, { ...msg(2), buffered: true }, 0);
    v = addTelemetry(v, { ...msg(3), buffered: true }, 0);
    expect(v.bufferedReceived).toBe(2);
    expect(v.missing).toBe(0);
  });

  it("marks alarm readings on the chart points", () => {
    let v = addTelemetry(emptyView, msg(1, "OK"), 0);
    v = addTelemetry(v, msg(2, "ALARM"), 0);
    expect(v.points.map((p) => p.alarm)).toEqual([false, true]);
  });
});

describe("formatDuration", () => {
  it("formats durations", () => {
    expect(formatDuration(45_000)).toBe("45 s");
    expect(formatDuration(192_000)).toBe("3 min 12 s");
    expect(formatDuration(3_900_000)).toBe("1 h 05 min");
  });
});

// ---------- alarm_state from the edge app (edge_app/alarms.py owns latch, acknowledge, cool-down) ----------
const T0 = Date.parse("2026-10-26T10:00:00Z");
const iso = (sec: number) => new Date(T0 + sec * 1000).toISOString();
const NORMAL: AlarmTypeInfo = { state: "NORMAL", acknowledged: false, start_ts: null, end_ts: null, peak: null,
                                cooldown_until: null, group: { count: 0, since: null } };
function alarmState(types: Record<string, Partial<AlarmTypeInfo>>, counters = { total: 1, false: 1, last_10min: 1, watched_s: 120 }): AlarmState {
  const all = Object.fromEntries(["tool_wear", "heat_dissipation", "power", "overstrain"]
    .map((t) => [t, { ...NORMAL, ...types[t] }]));
  const vals = Object.values(all);
  const latched = vals.some((t) => t.state === "ACTIVE" || t.state === "WAITING_FOR_ACK");
  const needsAck = vals.some((t) => t.state === "WAITING_FOR_ACK" || (t.state === "ACTIVE" && !t.acknowledged));
  return { ts: iso(0), latched, acknowledged: latched && !needsAck, active: vals.some((t) => t.state === "ACTIVE"),
           types: all, counters, cooldown_s: 60 };
}
const peak = (seq: number, type: string) => ({ ...msg(seq, "ALARM"), failure_type: type });

describe("alarm state is only displayed", () => {
  it("telemetry alone never latches (the edge app decides)", () => {
    let v = emptyView;
    for (const [seq, st] of [[1, "ALARM"], [2, "OK"]] as const) v = addTelemetry(v, msg(seq, st), 0);
    expect(v.latched).toBeNull();
    expect(v.alarms).toHaveLength(1); // the history row is still recorded
  });

  it("shows a latched alarm that is over but waits for an acknowledge", () => {
    const v = applyAlarmState(emptyView, alarmState({
      power: { state: "WAITING_FOR_ACK", start_ts: iso(1), end_ts: iso(4), peak: peak(2, "power") } }));
    expect(v.latched).toMatchObject({ type: "power", startTs: iso(1), active: false, endTs: iso(4), acknowledged: false });
  });

  it("shows the alarm that most needs the operator when several types are latched", () => {
    const v = applyAlarmState(emptyView, alarmState({
      power: { state: "ACTIVE", acknowledged: true, start_ts: iso(1), peak: peak(1, "power") },
      tool_wear: { state: "WAITING_FOR_ACK", start_ts: iso(5), end_ts: iso(6), peak: peak(5, "tool_wear") },
      overstrain: { state: "ACTIVE", start_ts: iso(8), peak: peak(8, "overstrain") },
    }));
    expect(v.latched?.type).toBe("overstrain"); // needs an acknowledge and is active
  });

  it("cool-down, counters and the alarm rate come from the edge app", () => {
    let v = addTelemetry(emptyView, { ...msg(1, "ALARM"), alarm_suppressed: true }, 0);
    v = applyAlarmState(v, alarmState({ tool_wear: { state: "COOLDOWN", acknowledged: true, cooldown_until: iso(63) } },
                                      { total: 300, false: 225, last_10min: 50, watched_s: 600 }));
    expect(v.latched).toBeNull();
    expect(v.cooldownUntil).toEqual({ tool_wear: T0 + 63_000 });
    expect(v.alarms[0].suppressed).toBe(true);
    expect([v.alarmTotal, v.alarmFalse]).toEqual([300, 225]);
    expect(alarmRatePerHour(v)).toBeCloseTo(300, 0);
    expect(alarmRatePerHour(applyAlarmState(v, alarmState({}, { total: 1, false: 1, last_10min: 1, watched_s: 30 })))).toBeNull();
  });

  it("two dashboards see the same state; an acknowledge from one clears both", () => {
    // Two browsers opened at different times: different telemetry history, same retained alarm_state.
    let a = addTelemetry(emptyView, msg(1, "OK"), 0);
    let b = emptyView;
    const waiting = alarmState({ power: { state: "WAITING_FOR_ACK", start_ts: iso(1), end_ts: iso(2), peak: peak(1, "power") } });
    a = applyAlarmState(a, waiting);
    b = applyAlarmState(b, waiting);
    expect(a.latched).toEqual(b.latched);
    expect(a.latched?.acknowledged).toBe(false);
    // Dashboard A sends {"cmd":"ack"}; the edge app publishes the new state to everyone.
    const acked = alarmState({ power: { state: "COOLDOWN", acknowledged: true, cooldown_until: iso(63) } });
    a = applyAlarmState(a, acked);
    b = applyAlarmState(b, acked);
    expect(a.latched).toBeNull();
    expect(b.latched).toBeNull();
    expect(a.cooldownUntil).toEqual(b.cooldownUntil);
  });
});
