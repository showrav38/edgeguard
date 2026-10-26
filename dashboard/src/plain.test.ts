import { describe, expect, it } from "vitest";
import { describeSignal, headline, mainReason } from "./plain";
import type { LatchedAlarm } from "./stream";
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

describe("headline", () => {
  it("green / amber / red", () => {
    expect(headline(reading(), null, true).light).toBe("green");
    expect(headline(reading({ state: "WARNING", failure_type: "tool_wear" }), null, true).light).toBe("amber");
    expect(headline(reading({ drift_warning: true }), null, true).light).toBe("amber");
    const latched: LatchedAlarm = {
      startSeq: 1, startTs: "", peak: reading({ state: "ALARM", failure_type: "tool_wear" }),
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
