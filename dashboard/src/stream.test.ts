import { describe, expect, it } from "vitest";
import { acknowledge, addTelemetry, emptyView, formatDuration, HISTORY_POINTS, SeqDeduper, type View } from "./stream";
import type { Telemetry } from "./types";

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

describe("alarm latch", () => {
  const feed = (v: View, states: Telemetry["state"][], start: number) =>
    states.reduce((acc, st, i) => addTelemetry(acc, msg(start + i, st), 0), v);

  it("stays latched after the readings go back to OK, until acknowledged", () => {
    let v = feed(emptyView, ["OK", "ALARM", "ALARM", "OK", "OK"], 1);
    expect(v.latched).toMatchObject({ startSeq: 2, active: false, endTs: msg(4).ts, acknowledged: false });
    v = acknowledge(v);
    expect(v.latched).toBeNull();
  });

  it("acknowledged while active: stays until the readings leave ALARM", () => {
    let v = feed(emptyView, ["ALARM", "ALARM"], 1);
    v = acknowledge(v);
    expect(v.latched).toMatchObject({ active: true, acknowledged: true });
    v = feed(v, ["ALARM"], 3);
    expect(v.latched?.acknowledged).toBe(true);
    v = feed(v, ["OK"], 4);
    expect(v.latched).toBeNull();
  });

  it("an alarm that returns before acknowledgement is the same latched alarm", () => {
    let v = feed(emptyView, ["ALARM", "OK", "ALARM"], 1);
    expect(v.latched).toMatchObject({ startSeq: 1, active: true, endTs: null });
  });

  it("keeps the highest-risk reading for the action", () => {
    let v = addTelemetry(emptyView, { ...msg(1, "ALARM"), risk: 0.5 }, 0);
    v = addTelemetry(v, { ...msg(2, "ALARM"), risk: 0.9 }, 0);
    v = addTelemetry(v, { ...msg(3, "ALARM"), risk: 0.6 }, 0);
    expect(v.latched?.peak.seq).toBe(2);
  });

  it("formats durations", () => {
    expect(formatDuration(45_000)).toBe("45 s");
    expect(formatDuration(192_000)).toBe("3 min 12 s");
    expect(formatDuration(3_900_000)).toBe("1 h 05 min");
  });
});
