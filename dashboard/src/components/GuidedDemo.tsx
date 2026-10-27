// "Guided demo": a 4-step tour driven by the existing demo commands (drift, speed, outage; see
// edge_app/commands.py). Each step sends its commands once, when you enter it, and then WAITS: it never
// moves on by itself, only "Next" does. A live line confirms what the dashboard saw (alarm raised,
// drift detected, readings lost).
// The panel lies exactly over the "Recent alarms" card (the one card the tour does not need), so it never
// covers the charts and opening or closing it never moves anything. Back / Next / Close stay in place.
import { IxButton, IxIconButton } from "@siemens/ix-react";
import { useEffect, useRef, useState } from "react";
import { PLAIN_FAILURE } from "../plain";
import type { View } from "../stream";
import type { Command, Status } from "../types";
import { ICON } from "./ui";

type Send = (c: Command) => void;

const NORMAL_MS = 1000; // 1 reading per second (the simulator default)
const FAST_MS = 100; // 10x: one tool life (~84 readings) in ~8 s
const BRISK_MS = 200; // 5x: drift is detected after ~30 readings = ~6 s; a 20 s outage buffers ~100 readings
const OUTAGE_S = 20;

const STEPS: { title: string; caption: string; enter: (send: Send) => void }[] = [
  {
    title: "Normal operation",
    caption: "The green light means Machine 1 runs normally. The risk gauge stays low. The tool-wear line climbs "
      + "slowly while the tool cuts and drops back to 0 when the tool is replaced.",
    enter: (send) => { send({ cmd: "drift", on: false }); send({ cmd: "speed", cycle_ms: NORMAL_MS }); },
  },
  {
    title: "Tool wearing out: alarm and action",
    caption: "We replay the machine 10 times faster. As the tool gets older the risk climbs. When it crosses the "
      + "alarm line, the light turns red and \"What to do\" says what to do and why. The alarm stays until "
      + "someone presses Acknowledge.",
    enter: (send) => { send({ cmd: "drift", on: false }); send({ cmd: "speed", cycle_ms: FAST_MS }); },
  },
  {
    title: "Unusual conditions (drift)",
    caption: "Now the shop floor gets 8 °C hotter and the spindle runs faster. The model never learned from data "
      + "like this, so EdgeGuard does not pretend to know: it warns that the prediction may be unreliable.",
    enter: (send) => { send({ cmd: "drift", on: true }); send({ cmd: "speed", cycle_ms: BRISK_MS }); },
  },
  {
    title: "Network outage, nothing lost",
    caption: `The connection to plant IT is cut for ${OUTAGE_S} s. EdgeGuard keeps watching the machine and stores `
      + "every reading on the edge device. When the connection is back it sends them all, in order.",
    enter: (send) => {
      send({ cmd: "drift", on: false });
      send({ cmd: "speed", cycle_ms: BRISK_MS });
      send({ cmd: "outage", seconds: OUTAGE_S });
    },
  },
];

export function GuidedDemo({ view, status, enabled, send, onClose }: {
  view: View;
  status: Status | null;
  enabled: boolean;
  send: Send;
  onClose: () => void;
}) {
  const [step, setStep] = useState(0);
  // What the dashboard had seen when the step started, so each step reports only its own events.
  const base = useRef({ alarmKey: "", missing: 0, buffered: 0 });
  const slowed = useRef(false);
  const [sawOutage, setSawOutage] = useState(false);
  const started = useRef(false);

  const alarmKey = view.alarms[0] ? `${view.alarms[0].seq}-${view.alarms[0].ts}` : "";

  const goTo = (i: number) => {
    base.current = { alarmKey, missing: view.missing, buffered: view.bufferedReceived };
    slowed.current = false;
    setSawOutage(false);
    setStep(i);
    STEPS[i].enter(send);
  };

  // Bring the panel into view once when it opens (it sits low on the page).
  const panel = useRef<HTMLElement>(null);
  useEffect(() => { panel.current?.scrollIntoView({ block: "nearest" }); }, []);

  // Enter step 1 once when the tour opens (the ref keeps React's dev double-mount from sending twice).
  useEffect(() => {
    if (enabled && !started.current) {
      started.current = true;
      goTo(0);
    }
  }); // no dependency list: runs after every render, but the ref makes it act only once

  const newAlarm = step === 1 && alarmKey !== "" && alarmKey !== base.current.alarmKey;
  const outageNow = status?.uplink?.state === "simulated_down";

  // Step 2: once the alarm is raised, slow back down to 1x so the "What to do" card can be read.
  useEffect(() => {
    if (newAlarm && !slowed.current) {
      slowed.current = true;
      send({ cmd: "speed", cycle_ms: NORMAL_MS });
    }
  }, [newAlarm, send]);

  useEffect(() => {
    if (step === 3 && outageNow) setSawOutage(true);
  }, [step, outageNow]);

  const finish = () => {
    if (enabled) {
      send({ cmd: "drift", on: false });
      send({ cmd: "speed", cycle_ms: NORMAL_MS });
    }
    onClose();
  };

  const m = view.latest;
  let live: string;
  if (!enabled) live = "The demo needs the edge app running (no heartbeat yet).";
  else if (!m) live = "Waiting for machine data...";
  else if (step === 0) live = `Live: risk ${Math.round(m.risk * 100)} %, tool used ${Math.round(m.signals.ToolWearMin)} min.`;
  else if (step === 1) {
    live = newAlarm
      // Name the same failure as the status card: the latched alarm's worst reading.
      ? `Alarm raised (${PLAIN_FAILURE[view.latched?.peak.failure_type ?? view.alarms[0].failureType]}). `
        + "Replay back to 1x so you can read \"What to do\"."
      : `Replaying 10x faster: tool used ${Math.round(m.signals.ToolWearMin)} min, risk ${Math.round(m.risk * 100)} %.`;
  } else if (step === 2) {
    live = m.drift_warning
      ? "Drift detected: the status line now says the conditions are unusual."
      : "Heating up the shop floor: the warning appears once enough readings look unusual.";
  } else {
    const up = status?.uplink;
    const delivered = view.bufferedReceived - base.current.buffered;
    const lost = view.missing - base.current.missing;
    if (outageNow) live = `Outage: ${Math.ceil(up?.outage_remaining_s ?? 0)} s left, ${up?.buffer_backlog ?? 0} readings stored on the device.`;
    else if (!sawOutage) live = "Cutting the connection...";
    else if ((up?.buffer_backlog ?? 0) > 10) live = `Connection back: sending ${up?.buffer_backlog} stored readings...`;
    else live = `Connection back: ${delivered} stored readings delivered in order, ${lost} lost.`;
  }

  const last = status?.last_command;
  const isLast = step === STEPS.length - 1;
  return (
    <aside ref={panel} className="tour" data-box="tour-panel" aria-label="Guided demo" aria-live="polite">
      <div className="tour-head">
        <span className="tour-steps" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
          {STEPS.map((_, i) => <span key={i} className={i === step ? "on" : i < step ? "done" : ""} />)}
        </span>
        <span className="small muted">Step {step + 1} of {STEPS.length} · simulated machine</span>
        <IxIconButton data-box="tour-close" icon={ICON.close} variant="subtle-tertiary" aria-label="End the guided demo"
                      onClick={finish} />
      </div>
      <h2 className="tour-title">{STEPS[step].title}</h2>
      <p className="tour-caption">{STEPS[step].caption}</p>
      <p className="tour-live num one-line">{last && !last.ok ? `Command rejected: ${last.error}` : live}</p>
      <div className="tour-nav">
        <IxButton data-box="tour-back" className="btn-nav" variant="subtle-secondary" icon={ICON.back}
                  disabled={step === 0 || !enabled} onClick={() => goTo(step - 1)}>Back</IxButton>
        <IxButton data-box="tour-next" className="btn-nav" variant="subtle-secondary" icon={isLast ? ICON.ack : ICON.next}
                  disabled={!enabled && !isLast} onClick={() => (isLast ? finish() : goTo(step + 1))}>
          {isLast ? "Finish" : "Next"}
        </IxButton>
      </div>
    </aside>
  );
}
