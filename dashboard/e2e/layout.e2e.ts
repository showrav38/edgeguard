// "Nothing moves": records the bounding box of EVERY card and button (all carry data-box) in both views,
// at desktop and phone width, through the states waiting, OK, attention, alarm, alarm cleared, drift,
// outage, buffer draining, acknowledged, many alarms and paused, and fails if any element appears,
// disappears, moves or changes size by more than 2 px. On a phone, in both views: no text cut off, no sideways scrolling.
//
// The data comes from a private broker (started by e2e/run.sh); this test publishes the same messages the edge app would,
// including alarm_state (the edge app owns latch, acknowledge and cool-down) and its answer to the Acknowledge command.
import { expect, test, type Page } from "@playwright/test";
import mqtt from "mqtt";

const TOLERANCE_PX = 2;
const TICK_WAIT_MS = 1300; // the screen updates once per second
const T = "edgeguard/machine1";

type Box = { x: number; y: number; w: number; h: number };

// ---------- messages, shaped like edge_app/main.py ----------
let uplink = { state: "up", outage_remaining_s: 0, ack_ms: 3.1, buffer_backlog: 0, buffer_dropped: 0, buffer_max: 100000 };
const status = () => ({
  state: "running", ts: new Date().toISOString(), model_version: "1.0.0", threshold: 0.16, cost_ratio: 20,
  plc: { connected: true, last_seq: seq, io_ms: 4.2, missed_cycles: 0, drift_mode: false, cycle_ms: 1000 },
  edge: { inference_ms: 0.6 }, uplink, last_command: null,
});

// edgeguard/robot_cell/status, shaped like ros2/edgeguard_ros/edgeguard_ros/robot_sim.py
let robot = { state: "running", reason: "running", parts: 12 };
const robotStatus = () => ({ ...robot, ts: new Date().toISOString() });
const ROBOT_STOPPED = { state: "stopped", reason: "stopped by EdgeGuard", parts: 1234 };
const ROBOT_RUNNING = { state: "running", reason: "running", parts: 1234 };

// edgeguard/machine1/alarm_state, shaped like edge_app/alarms.py. One failure type changes at a time
// (tool_wear, except in the "longest text" steps at the end).
const NORMAL_TYPE = { state: "NORMAL", acknowledged: false, start_ts: null as string | null, end_ts: null as string | null,
                      peak: null as object | null, cooldown_until: null as string | null, group: { count: 0, since: null as string | null } };
let toolWear = { ...NORMAL_TYPE };
let alarmType = "tool_wear"; // the type `toolWear` stands for
let counters = { total: 0, false: 0, last_10min: 0, watched_s: 0 };
const alarmState = () => {
  const latched = toolWear.state === "ACTIVE" || toolWear.state === "WAITING_FOR_ACK";
  return { ts: new Date().toISOString(), latched, acknowledged: latched && toolWear.acknowledged, active: toolWear.state === "ACTIVE",
           types: { tool_wear: NORMAL_TYPE, heat_dissipation: NORMAL_TYPE, power: NORMAL_TYPE, overstrain: NORMAL_TYPE,
                    [alarmType]: toolWear },
           counters, cooldown_s: 60 };
};

let seq = 0;
function reading(o: { risk?: number; state?: string; type?: string; code?: number; text?: string; wear?: number;
                      drift?: boolean; buffered?: boolean; truth?: boolean } = {}) {
  seq += 1;
  const state = o.state ?? "OK";
  return {
    seq, ts: new Date(Date.now() + seq).toISOString(),
    signals: { AirTempK: o.drift ? 308.4 : 300.4, ProcessTempK: o.drift ? 318.6 : 310.6, SpeedRpm: o.drift ? 1810 : 1510,
               TorqueNm: 41.3, ToolWearMin: o.wear ?? 120, ProductType: "L" },
    risk: o.risk ?? 0.01, alarm: state === "ALARM", state, failure_type: o.type ?? "none",
    action: { code: o.code ?? 0, text: o.text ?? "No action needed." },
    top_factors: state === "OK" ? [{ signal: "TorqueNm", effect: 0.001 }, { signal: "SpeedRpm", effect: 0.0 }]
      : [{ signal: "ToolWearMin", effect: 0.14 }, { signal: "TorqueNm", effect: 0.05 }],
    drift_score: o.drift ? 3.2 : 0.8, drift_warning: !!o.drift, drift_signals: o.drift ? ["AirTempK", "ProcessTempK", "SpeedRpm"] : [],
    inference_ms: 0.61, plc_io_ms: 4.2, buffered: !!o.buffered, buffer_backlog: 0, buffer_dropped: 0,
    ground_truth: { failure: !!o.truth, type: o.truth ? "TWF" : "none" }, missed_cycles: 0,
  };
}
const ALARM = { risk: 0.31, state: "ALARM", type: "tool_wear", code: 1, wear: 214, truth: true,
                text: "Tool wear 214 min: replace the tool before the next batch." };
const WARNING = { risk: 0.1, state: "WARNING", type: "tool_wear", code: 5, wear: 196,
                  text: "Risk rising (tool wear): watch the machine and plan a check at the next stop." };

// ---------- measuring ----------
async function boxes(page: Page): Promise<Record<string, Box>> {
  return page.evaluate(() => {
    const out: Record<string, Box> = {};
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("[data-box]"))) {
      const key = el.dataset.box!;
      if (key in out) throw new Error(`duplicate data-box "${key}"`);
      const r = el.getBoundingClientRect();
      out[key] = { x: r.x + window.scrollX, y: r.y + window.scrollY, w: r.width, h: r.height };
    }
    // Every card and button must be measured: none may lack a data-box name.
    const unnamed = Array.from(document.querySelectorAll(".card, button, ix-button, ix-icon-button, ix-toggle-button"))
      .filter((el) => !(el as HTMLElement).dataset.box);
    // Nothing may stick out sideways (a cut-off button is as bad as a moving one).
    const wide = Array.from(document.querySelectorAll<HTMLElement>("[data-box]"))
      .filter((el) => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1);
    if (wide.length) throw new Error(`outside the screen width: ${wide.map((e) => e.dataset.box).join(", ")}`);
    if (unnamed.length) throw new Error(`card/button without data-box: ${unnamed.map((e) => e.outerHTML.slice(0, 80)).join(" | ")}`);
    return out;
  });
}

/** Text that does not fit: an element that clips (overflow hidden / ellipsis / line clamp) or scrolls sideways
 *  while its content is wider or taller than its box. Lists that scroll DOWN inside a card are allowed.
 *  `root`: where to look (default the whole page content). */
async function cutOff(page: Page, root = "main"): Promise<string[]> {
  return page.evaluate((root) => {
    const out: string[] = [];
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth + 1) out.push(`page scrolls sideways (${doc.scrollWidth} > ${doc.clientWidth} px)`);
    const name = (el: Element) => `${(el.closest("[data-box]") as HTMLElement | null)?.dataset.box ?? "?"} `
      + `<${el.tagName.toLowerCase()} class="${el.getAttribute("class") ?? ""}"> "${(el.textContent ?? "").trim().slice(0, 40)}"`;
    for (const el of Array.from(document.querySelectorAll(`${root} *`))) {
      if (el.closest("svg, .recharts-wrapper")) continue;
      const cs = getComputedStyle(el);
      if (cs.overflowX !== "visible" && el.scrollWidth > el.clientWidth + 1) {
        out.push(`${name(el)}: ${el.scrollWidth} px wide in ${el.clientWidth} px`);
      }
      if ((cs.overflowY === "hidden" || cs.overflowY === "clip") && el.scrollHeight > el.clientHeight + 1) {
        out.push(`${name(el)}: ${el.scrollHeight} px tall in ${el.clientHeight} px`);
      }
    }
    return out;
  }, root);
}


/** Differences vs. the baseline: missing, extra or moved / resized elements. */
function compare(base: Record<string, Box>, now: Record<string, Box>): string[] {
  const problems: string[] = [];
  for (const [k, b] of Object.entries(base)) {
    const n = now[k];
    if (!n) { problems.push(`${k}: disappeared`); continue; }
    const d = Math.max(Math.abs(n.x - b.x), Math.abs(n.y - b.y), Math.abs(n.w - b.w), Math.abs(n.h - b.h));
    if (d > TOLERANCE_PX) {
      problems.push(`${k}: moved/resized by ${d.toFixed(1)} px (${fmt(b)} -> ${fmt(n)})`);
    }
  }
  for (const k of Object.keys(now)) if (!(k in base)) problems.push(`${k}: appeared`);
  return problems;
}
const fmt = (b: Box) => `${b.x.toFixed(0)},${b.y.toFixed(0)} ${b.w.toFixed(0)}x${b.h.toFixed(0)}`;

// ---------- the scenario ----------
for (const viewport of [{ name: "desktop", width: 1366, height: 900 }, { name: "phone", width: 390, height: 844 }]) {
  for (const mode of ["operator", "engineer"] as const) {
    test(`${mode} view, ${viewport.name}: nothing moves`, async ({ page }) => {
      seq = 0;
      uplink = { ...uplink, state: "up", buffer_backlog: 0, outage_remaining_s: 0 };
      robot = { state: "running", reason: "running", parts: 12 };
      toolWear = { ...NORMAL_TYPE };
      alarmType = "tool_wear";
      counters = { total: 0, false: 0, last_10min: 0, watched_s: 0 };
      const client = await mqtt.connectAsync("ws://localhost:19001", { clientId: `layout-${mode}-${viewport.name}` });
      // Like the edge app: publish the alarm state (retained) at once when it changes.
      const setAlarm = async (patch: Partial<typeof toolWear>, total?: number) => {
        toolWear = { ...toolWear, ...patch };
        if (total != null) counters = { ...counters, total, last_10min: total, watched_s: 120 };
        await client.publishAsync(`${T}/alarm_state`, JSON.stringify(alarmState()), { qos: 1, retain: true });
      };
      // Like the edge app: answer the dashboard's Acknowledge (the only command this test expects).
      const acks: { cmd: string; type: string | null }[] = [];
      await client.subscribeAsync(`${T}/cmd`, { qos: 1 });
      client.on("message", (topic, payload) => {
        const cmd = JSON.parse(payload.toString());
        if (topic !== `${T}/cmd` || cmd.cmd !== "ack") return;
        acks.push(cmd);
        void setAlarm({ acknowledged: true, cooldown_until: new Date(Date.now() + 60_000).toISOString() });
      });
      await setAlarm({});
      // Clear retained status from an earlier test, then keep a fresh heartbeat going (the dashboard needs one < 3 s old).
      await client.publishAsync(`${T}/status`, "", { retain: true });
      await client.publishAsync("edgeguard/robot_cell/status", "", { retain: true });
      const beat = setInterval(() => {
        client.publish(`${T}/status`, JSON.stringify(status()), { retain: true });
        client.publish("edgeguard/robot_cell/status", JSON.stringify(robotStatus()), { retain: true });
      }, 500);
      const send = async (...ms: object[]) => {
        for (const m of ms) await client.publishAsync(`${T}/telemetry`, JSON.stringify(m), { qos: 1 });
      };

      try {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.addInitScript((m) => localStorage.setItem("edgeguard.view", m), mode);
        await page.goto("/");
        await page.waitForTimeout(2500); // connect + first heartbeat + first tick

        const results: { state: string; problems: string[] }[] = [];
        const base = await boxes(page);
        // expect: a text that proves the state really reached the screen (else "stable" would mean nothing).
        // No text cut off and no sideways scrolling on a phone, in both views (every card).
        const strictText = viewport.name === "phone";
        const check = async (state: string, expectText?: string) => {
          await page.waitForTimeout(TICK_WAIT_MS);
          if (expectText) await expect(page.locator("main")).toContainText(new RegExp(expectText.replace(/[()]/g, "\\$&"), "i"));
          const now = await boxes(page);
          const problems = compare(base, now);
          if (strictText) problems.push(...await cutOff(page));
          results.push({ state, problems });
          // Diagnostic picture (not compared). Grow the viewport instead of fullPage: fullPage's own resize
          // makes the charts redraw during the capture and they can come out blank.
          const height = await page.evaluate(() => document.documentElement.scrollHeight);
          await page.setViewportSize({ width: viewport.width, height });
          await page.waitForTimeout(500);
          await page.screenshot({ path: `test-results/layout/${mode}-${viewport.name}-${state.replace(/[^a-z0-9]+/gi, "_")}.png` });
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
        };
        expect(Object.keys(base).length).toBeGreaterThan(10);

        for (let i = 0; i < 30; i++) await send(reading({ wear: 60 + 4 * i }));
        await check("OK", mode === "operator" ? "running normally" : "OK");
        await send(reading(WARNING));
        await check("attention", mode === "operator" ? "Attention soon" : "WARNING");
        const first = reading(ALARM);
        await send(first, reading(ALARM));
        await setAlarm({ state: "ACTIVE", start_ts: first.ts, peak: first, group: { count: 1, since: first.ts } }, 1);
        robot = ROBOT_STOPPED;
        await check("alarm", mode === "operator" ? "likely to fail soon" : "ALARM");
        if (mode === "operator") await expect(page.locator("main")).toContainText("Robot cell stopped until you acknowledge");
        const cleared = reading({ wear: 216 });
        await send(cleared);
        await setAlarm({ state: "WAITING_FOR_ACK", end_ts: cleared.ts });
        await check("alarm cleared, not acknowledged", "needs your check");
        await send(...Array.from({ length: 5 }, () => reading({ drift: true, wear: 20 })));
        await check("drift", mode === "operator" ? "unusual conditions" : "Drift: predictions may be unreliable");
        uplink = { ...uplink, state: "simulated_down", outage_remaining_s: 14, buffer_backlog: 57 };
        robot = { state: "stopped", reason: "stopped: no interlock signal", parts: 1234 };
        await check("outage", mode === "engineer" ? "outage (simulated)" : undefined);
        uplink = { ...uplink, state: "up", outage_remaining_s: 0, buffer_backlog: 43 };
        await send(...Array.from({ length: 5 }, () => reading({ buffered: true, wear: 30 })));
        robot = ROBOT_STOPPED;
        await check("buffer draining", mode === "engineer" ? "sending 43 stored" : undefined);
        uplink = { ...uplink, buffer_backlog: 0 };
        await send(reading(ALARM));
        await setAlarm({ state: "ACTIVE", end_ts: null, group: { count: 2, since: first.ts } }, 2);
        await page.waitForTimeout(TICK_WAIT_MS);
        await page.locator(`[data-box="ack-${mode}"]`).click();
        robot = ROBOT_RUNNING;
        await check("alarm acknowledged", "Acknowledged");
        if (mode === "operator") await expect(page.locator("main")).toContainText("Robot cell: running (1,234 parts)");
        expect(acks.map((a) => a.type)).toEqual(["tool_wear"]); // the dashboard asked the edge app
        // Cool-down: the edge app records and counts the repeats but does not latch them (alarm_suppressed).
        for (let i = 0; i < 10; i++) {
          await send({ ...reading({ ...ALARM, truth: i % 2 === 0 }), alarm_suppressed: true }, reading({ wear: 10 }));
        }
        await setAlarm({ state: "COOLDOWN", start_ts: null, peak: null, group: { count: 12, since: first.ts } }, 12);
        await check("many alarms (cool-down)", mode === "operator" ? "alarms in the last" : "no, cool-down");
        await send({ ...reading(ALARM), alarm_suppressed: true });
        await setAlarm({ group: { count: 13, since: first.ts } }, 13);
        await check("repeat during cool-down", mode === "operator" ? "Worn-out tool: \\d+ alarms in the last" : "ALARM again, in cool-down");
        await send(reading({ wear: 10 }));
        await page.locator('[data-box="pause"]').click();
        await check("paused");
        await page.locator('[data-box="pause"]').click();
        await check("resumed");
        // Longest texts: a latched alarm of every failure type (its reason sentence is the longest text on the
        // Operator view; heat dissipation's is the longest of all), so cut-off text shows up on a phone.
        for (const [type, code, wear] of [["heat_dissipation", 2, 120], ["overstrain", 4, 214], ["power", 3, 120]] as const) {
          const peak = reading({ ...ALARM, type, code, wear, text: "" });
          await send(peak);
          alarmType = type;
          toolWear = { ...NORMAL_TYPE, state: "ACTIVE", start_ts: peak.ts, peak, group: { count: 1, since: peak.ts } };
          await setAlarm({});
          await check(`longest text: ${type}`, "likely to fail soon|ALARM");
        }

        const failed = results.filter((r) => r.problems.length);
        for (const r of results) console.log(`  ${mode}/${viewport.name} ${r.state}: ${r.problems.length ? "MOVED" : "stable"}`);
        expect(failed, failed.map((r) => `${r.state}:\n    ${r.problems.join("\n    ")}`).join("\n")).toEqual([]);
      } finally {
        clearInterval(beat);
        await client.endAsync();
      }
    });
  }
}
