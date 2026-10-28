// Operator view: readable in 5 seconds by a technician or a visitor. Plain words, one traffic light,
// one action, one gauge. The numbers behind it are in the Engineer view.
// Every card has a fixed size and every button is always there (disabled when not usable), so
// nothing moves when the machine state changes.
import { IxButton } from "@siemens/ix-react";
import { groupAlarms, groupSentence, headline, mainReason, PLAIN_ACTION, PLAIN_FAILURE, robotLine, shownReading, type Light } from "../plain";
import { formatDuration, type AlarmEvent, type LatchedAlarm, type View } from "../stream";
import type { RobotStatus } from "../types";
import { Card, GLOSSARY, Help, ICON, StateLabel, Waiting, type Tone } from "./ui";

export const LIGHT_TONE: Record<Light, Tone> = { green: "ok", amber: "warn", red: "bad", off: "idle" };
const clock = (ts: string | number) => new Date(ts).toLocaleTimeString([], { hour12: false });

/** The one line under the status sentence: the latched alarm's time, the cool-down, or why there is nothing to acknowledge. */
export function latchLine(view: View, now: number): string {
  const { latched, latest } = view;
  if (!latched) {
    // The newest cool-down still running (after an Acknowledge, the same type does not latch again).
    const running = Object.entries(view.cooldownUntil).filter(([, until]) => until > now).sort((a, b) => b[1] - a[1])[0];
    if (!running) return "No alarm waiting for acknowledgement.";
    const what = PLAIN_FAILURE[running[0]] ?? running[0];
    return latest?.state === "ALARM" && latest.failure_type === running[0]
      ? `Repeats of ${what} until ${clock(running[1])} are listed in the history, not raised again.`
      : `Acknowledged. Repeats of ${what} until ${clock(running[1])} go to the history only.`;
  }
  if (latched.acknowledged) return "Acknowledged. The light turns green when the readings are normal again.";
  if (latched.active) return `Alarm since ${clock(latched.startTs)} (${formatDuration(now - Date.parse(latched.startTs))}).`;
  return `Alarm ${clock(latched.startTs)} to ${clock(latched.endTs!)}. Readings are normal again: check, then Acknowledge.`;
}

/** Acknowledge is usable only for a latched, not yet acknowledged alarm, on a live (not paused) screen. */
export const canAck = (latched: LatchedAlarm | null, live: boolean, paused: boolean) =>
  !!latched && !latched.acknowledged && live && !paused;

/** The traffic light, the one sentence and the Acknowledge button (always in the same place). */
export function StatusHero({ view, robot, live, paused, now, onAck }: {
  view: View;
  robot: RobotStatus | null;
  live: boolean;
  paused: boolean;
  now: number;
  onAck: () => void;
}) {
  const { latest, latched } = view;
  const group = latest ? view.alarmState?.types[latest.failure_type]?.group ?? null : null;
  const h = headline(latest, latched, live, group, now);
  const tone = LIGHT_TONE[h.light];
  // The sentence already says "unusual conditions" when drift is the only thing going on.
  const showDrift = live && !!latest?.drift_warning && (!!latched || latest.state !== "OK");
  const r = robotLine(robot, now);
  return (
    <section className={`card hero card-${tone}`} data-box="hero" aria-live="polite">
      <div className="lights" aria-hidden="true">
        {(["red", "amber", "green"] as const).map((c) => (
          <span key={c} className={`lamp lamp-${c}${h.light === c ? " on" : ""}`} />
        ))}
      </div>
      <div className="hero-body">
        <StateLabel tone={tone} big>{h.word}</StateLabel>
        <p className="hero-sentence">{h.sentence}</p>
        <p className="hero-latch num">{live ? latchLine(view, now) : " "}</p>
        {/* Drift must stay visible even while an alarm is shown above it. The line is always reserved. */}
        <p className="hero-note">
          {showDrift ? <StateLabel tone="warn">Unusual conditions: the prediction may be wrong.</StateLabel> : " "}
        </p>
        {/* Robot cell (ROS 2 interlock, D-31/D-32): always this one line, only text and colour change. */}
        <p className="hero-robot" data-box="hero-robot"><StateLabel tone={r.tone}>{r.text}</StateLabel></p>
      </div>
      <div className="hero-action">
        <IxButton data-box="ack-operator" className="btn-ack" variant={canAck(latched, live, paused) ? "primary" : "subtle-secondary"}
                  icon={ICON.ack} disabled={!canAck(latched, live, paused)} onClick={onAck}>Acknowledge</IxButton>
        <Help term="Acknowledge" box="help-ack-operator">{GLOSSARY.acknowledge}</Help>
      </div>
    </section>
  );
}

/** What to do, and why, in one sentence each. No status colour: the status card is the one place for it. */
export function WhatToDo({ view, live }: { view: View; live: boolean }) {
  const { latest, latched } = view;
  if (!latest || !live) {
    return <Card box="todo" title="What to do" icon={ICON.todo} className="todo"><Waiting /></Card>;
  }
  const m = shownReading(latest, latched);
  const reason = mainReason(m)
    ?? (m.state === "OK" ? "All readings are in their usual range." : "No single reading stands out.");
  return (
    <Card box="todo" title="What to do" icon={ICON.todo} className="todo">
      <p className="todo-action">{PLAIN_ACTION[m.action.code] ?? m.action.text}</p>
      <p className="todo-reason"><strong>Why: </strong>{reason}</p>
      <p className="small muted num">
        {latched ? `From the alarm's worst reading at ${clock(m.ts)}.` : `Based on the latest reading at ${clock(m.ts)}.`}
      </p>
    </Card>
  );
}

/** Half-circle gauge. It spans 0 to 2 x threshold so the three zones are readable even when the
 *  threshold is low (16 %): green below half the threshold, amber up to it, red above. */
export function RiskGauge({ risk, threshold, costRatio }: { risk: number | null; threshold?: number; costRatio?: number }) {
  const max = threshold != null ? Math.min(1, 2 * threshold) : 1;
  const t = threshold ?? 0.5;
  const R = 80;
  const frac = (v: number) => Math.min(1, Math.max(0, v / max));
  // Point on the half circle: f = 0 is the left end, f = 1 the right end (centre 100,100).
  const at = (f: number, r = R) => {
    const a = Math.PI * (1 - f);
    return { x: 100 + r * Math.cos(a), y: 100 - r * Math.sin(a) };
  };
  const arc = (f1: number, f2: number) => {
    const p1 = at(f1), p2 = at(f2);
    return `M ${p1.x} ${p1.y} A ${R} ${R} 0 0 1 ${p2.x} ${p2.y}`;
  };
  const needle = risk != null ? at(frac(risk), R - 22) : null;
  return (
    <Card box="gauge" title="Failure risk now" icon={ICON.gauge} className="gauge-card"
          help={<Help term="Risk" box="help-risk-gauge">{GLOSSARY.risk}</Help>}>
      <svg viewBox="0 0 200 112" className="gauge" role="img"
           aria-label={risk != null ? `Risk ${Math.round(risk * 100)} percent, alarm at ${Math.round(t * 100)} percent` : "No risk value yet"}>
        <path d={arc(0, frac(t / 2))} className="g-ok" />
        <path d={arc(frac(t / 2), frac(t))} className="g-warn" />
        <path d={arc(frac(t), 1)} className="g-bad" />
        {needle && <line x1="100" y1="100" x2={needle.x} y2={needle.y} className="g-needle" />}
        <circle cx="100" cy="100" r="5" className="g-hub" />
      </svg>
      <div className="gauge-value num">{risk != null ? `${Math.round(risk * 100)} %` : "–"}</div>
      <p className="gauge-foot small muted num">
        Alarm at {Math.round(t * 100)} %
        <Help term="Alarm threshold" box="help-threshold-gauge">{GLOSSARY.threshold(threshold, costRatio)}</Help>
      </p>
    </Card>
  );
}

const lasted = (a: AlarmEvent) => (a.endTs ? formatDuration(Date.parse(a.endTs) - Date.parse(a.ts)) : null);

/** Alarm history as short sentences, newest first; repeats of one type are one line with a count. Scrolls inside. */
export function AlarmStory({ alarms, total, now }: { alarms: AlarmEvent[]; total: number; now: number }) {
  return (
    <Card box="alarms-operator" title="Recent alarms" icon={ICON.history} className="history">
      {alarms.length === 0 ? <p className="muted">No alarms yet.</p> : (
        <ul className="story">
          {groupAlarms(alarms).slice(0, 8).map((g) => {
            const newest = g.events[0];
            const cooled = g.events.filter((a) => a.suppressed).length;
            return (
              <li key={`${newest.seq}-${newest.ts}`} className={newest.endTs ? "" : "story-active"}>
                {groupSentence(g, now, lasted(newest))}
                {g.events.some((a) => a.buffered) && <span className="tag">delivered late</span>}
                {cooled > 0 && <span className="tag">{cooled} during cool-down</span>}
              </li>
            );
          })}
        </ul>
      )}
      <p className="small muted card-foot">
        {total} alarm{total === 1 ? "" : "s"} counted. "Replay check" compares with what really happened in the recorded data.
      </p>
    </Card>
  );
}
