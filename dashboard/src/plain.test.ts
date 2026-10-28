import { describe, expect, it } from "vitest";
import { describeSignal, groupAlarms, groupHeadline, groupSentence, headline, mainReason, typeReason, robotLine } from "./plain";
import type { AlarmEvent, LatchedAlarm } from "./stream";
import type { Telemetry } from "./types";

function reading(over: Partial<Telemetry> = {}): Telemetry {
  return {
    seq: 1, ts: "2026-10-26T10:00:00Z", state: "OK", risk: 0.01, alarm: false,
    signals: { AirTempK: 300, ProcessTempK: 310, SpeedRpm: 1500, TorqueNm: 40, ToolWearMin: 212, ProductType: "L" },
    failure_type: "none", action: { code: 0, text: "" }, top_factors: [], drift_score: null,
    drift_warning: false, drift_signals: [], inference_ms: 1, plc_io_ms: 5, buffered: false,
    buffer_backlog: 0, buffer_dropped: 0, ground_truth: { failure: false, type: "none" }, missed_cycles: 0,
    ...over,
  };
}

describe("describeSignal", () => {
  it("tool wear against the ~200 min limit", () => {
    expect(describeSignal("ToolWearMin", 212)).toBe("Tool used 212 min, normal limit about 200 min.");
    expect(describeSignal("ToolWearMin", 190)).toContain("getting close");
  });

  it("temperatures in °C with the usual range", () => {
    expect(describeSignal("AirTempK", 308.15)).toBe("Air temperature 35 °C, higher than usual (normally 24 °C to 30 °C).");
  });
});

describe("mainReason", () => {
  it("uses the top factor of a non-OK reading", () => {
    const m = reading({ state: "ALARM", top_factors: [{ signal: "ToolWearMin", effect: 0.4 }] });
    expect(mainReason(m)).toBe("Tool used 212 min, normal limit about 200 min.");
  });

  it("says nothing when the machine is OK", () => {
    expect(mainReason(reading({ top_factors: [{ signal: "ToolWearMin", effect: 0.4 }] }))).toBeNull();
  });
});

// The explanation's top factors for a real power failure (seq 1614, 9.01 kW, docs/decisions.md D-11):
// torque and process temperature. The reason must still talk about power.
const POWER_FACTORS = [{ signal: "TorqueNm", effect: 0.3 }, { signal: "ProcessTempK", effect: 0.1 }];

describe("the Why reason matches the failure type", () => {
  const alarm = (failure_type: string, signals: Partial<Telemetry["signals"]>) => reading({
    state: "ALARM", failure_type, top_factors: POWER_FACTORS,
    signals: { AirTempK: 300, ProcessTempK: 310, SpeedRpm: 1500, TorqueNm: 40, ToolWearMin: 100, ProductType: "L", ...signals },
  });

  it("power -> power (torque x speed) against 3.5 to 9 kW", () => {
    expect(mainReason(alarm("power", { TorqueNm: 60, SpeedRpm: 1434 })))
      .toBe("Power 9.01 kW (60 Nm at 1,434 rpm), above the usual maximum of 9 kW.");
    expect(mainReason(alarm("power", { TorqueNm: 20, SpeedRpm: 1400 })))
      .toBe("Power 2.93 kW (20 Nm at 1,400 rpm), below the usual minimum of 3.5 kW.");
    expect(mainReason(alarm("power", { TorqueNm: 55, SpeedRpm: 1450 }))).toContain("close to the 9 kW maximum");
  });

  it("tool wear -> tool use time", () => {
    expect(mainReason(alarm("tool_wear", { ToolWearMin: 212 }))).toBe("Tool used 212 min, normal limit about 200 min.");
  });

  it("heat dissipation -> temperature difference and speed", () => {
    expect(mainReason(alarm("heat_dissipation", { AirTempK: 302, ProcessTempK: 310, SpeedRpm: 1320 })))
      .toBe("Process only 8.0 °C warmer than the air at 1,320 rpm: too little to carry the heat away "
        + "(needs over 8.6 °C or over 1,380 rpm).");
  });

  it("overstrain -> tool wear x torque against the product's limit", () => {
    expect(mainReason(alarm("overstrain", { ToolWearMin: 200, TorqueNm: 60, ProductType: "L" })))
      .toBe("Load 12,000 min·Nm (tool use × torque), above the limit of about 11,000 for product L.");
    expect(mainReason(alarm("overstrain", { ToolWearMin: 200, TorqueNm: 60, ProductType: "H" })))
      .toContain("close to the limit of about 13,000 for product H");
  });

  it("every failure type has its own reason, none falls back to the top factor", () => {
    for (const t of ["tool_wear", "heat_dissipation", "power", "overstrain"]) {
      expect(typeReason(alarm(t, {})), t).not.toBeNull();
      expect(mainReason(alarm(t, {})), t).not.toMatch(/^Torque/);
    }
  });

  it("a WARNING uses its type's reason too; an unknown type falls back to the top factor", () => {
    expect(mainReason(reading({ state: "WARNING", failure_type: "power", top_factors: POWER_FACTORS })))
      .toMatch(/^Power /);
    expect(mainReason(reading({ state: "WARNING", failure_type: "none", top_factors: POWER_FACTORS })))
      .toMatch(/^Torque /);
  });
});

const event = (seq: number, sec: number, failureType: string, actualFailure = false): AlarmEvent => ({
  seq, ts: new Date(Date.parse("2026-10-26T10:00:00Z") + sec * 1000).toISOString(), endTs: null, failureType,
  risk: 0.2 + seq / 100, action: "", actualFailure, actualType: actualFailure ? "PWF" : "none", buffered: false,
  suppressed: false,
});
const NOW = Date.parse("2026-10-26T10:00:00Z");

describe("alarm groups", () => {
  it("groups repeats of one type, keeps every alarm, newest group first", () => {
    // newest first: power 300 s, tool_wear 250 s, power 200 s, power 30 s, power 0 s (all < 5 min apart)
    const alarms = [event(5, 300, "power"), event(4, 250, "tool_wear"), event(3, 200, "power", true),
                    event(2, 30, "power"), event(1, 0, "power")];
    const groups = groupAlarms(alarms);
    expect(groups.map((g) => [g.failureType, g.events.map((a) => a.seq)])).toEqual([
      ["power", [5, 3, 2, 1]], ["tool_wear", [4]]]);
    expect(groups.flatMap((g) => g.events)).toHaveLength(alarms.length);
    expect(groupHeadline(groups[0], NOW + 330_000)).toBe("Power problem: 4 alarms in the last 6 min");
    expect(groupSentence(groups[0], NOW + 330_000, null))
      .toBe("Power problem: 4 alarms in the last 6 min (highest risk 25 %). Replay check: 1 real, 3 false.");
  });

  it("a gap of more than 5 min starts a new group", () => {
    const groups = groupAlarms([event(2, 400, "power"), event(1, 0, "power")]);
    expect(groups.map((g) => g.events.length)).toEqual([1, 1]);
  });
});

describe("headline", () => {
  it("red only while the alarm is active; over but not acknowledged is amber", () => {
    const peak = reading({ state: "ALARM", failure_type: "power", ts: "2026-10-26T10:00:05Z" });
    const base: LatchedAlarm = { type: "power", startSeq: 1, startTs: "2026-10-26T10:00:05Z", peak, active: true, endTs: null, acknowledged: false };
    expect(headline(reading({ state: "ALARM" }), base, true).light).toBe("red");
    expect(headline(reading({ state: "ALARM" }), { ...base, acknowledged: true }, true).light).toBe("red");
    const over = headline(reading(), { ...base, active: false, endTs: "2026-10-26T10:00:09Z" }, true);
    expect(over.light).toBe("amber");
    expect(over.sentence).toMatch(/^Alarm at \d\d:\d\d:05 needs your check \(power problem\)\.$/);
  });

  it("a repeat during the cool-down (ALARM, no latch) is amber and counted", () => {
    const latest = reading({ state: "ALARM", failure_type: "power" });
    // The count is the edge app's group for this type (alarm_state), not this browser's history.
    const h = headline(latest, null, true, { count: 3, since: "2026-10-26T10:00:00Z" }, NOW + 150_000);
    expect(h).toEqual({ light: "amber", word: "Attention", sentence: "Power problem: 3 alarms in the last 3 min" });
    expect(headline(latest, null, true, null, NOW).sentence).toBe("Power problem again, already acknowledged.");
  });

  it("green / amber / red", () => {
    expect(headline(reading(), null, true).light).toBe("green");
    expect(headline(reading({ state: "WARNING", failure_type: "tool_wear" }), null, true).light).toBe("amber");
    expect(headline(reading({ drift_warning: true }), null, true).light).toBe("amber");
    const latched: LatchedAlarm = {
      type: "tool_wear", startSeq: 1, startTs: "", peak: reading({ state: "ALARM", failure_type: "tool_wear" }),
      active: true, endTs: null, acknowledged: false,
    };
    const h = headline(reading(), latched, true);
    expect(h.light).toBe("red");
    expect(h.sentence).toBe("Machine 1 is likely to fail soon: worn-out tool.");
  });

  it("off when there is no live data", () => {
    expect(headline(reading(), null, false).light).toBe("off");
  });
});

describe("robot cell line", () => {
  const ts = "2026-10-26T10:00:00Z";
  const at = Date.parse(ts) + 500;
  it("running with parts, stopped until acknowledge, offline when stale", () => {
    expect(robotLine({ state: "running", reason: "running", parts: 1234, ts }, at))
      .toEqual({ tone: "ok", text: "Robot cell: running (1,234 parts)" });
    expect(robotLine({ state: "stopped", reason: "stopped by EdgeGuard", parts: 5, ts }, at).text)
      .toBe("Robot cell stopped until you acknowledge");
    expect(robotLine({ state: "stopped", reason: "stopped: no interlock signal", parts: 5, ts }, at).text)
      .toBe("Robot cell stopped: no interlock signal");
    expect(robotLine({ state: "running", reason: "running", parts: 1, ts }, at + 5000).text).toBe("Robot cell: offline");
    expect(robotLine(null, at).text).toBe("Robot cell: no data");
  });
});
