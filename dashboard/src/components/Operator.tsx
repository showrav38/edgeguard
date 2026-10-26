// Operator view: readable in 5 seconds by a technician or a visitor. Plain words, one traffic light,
// one action, one gauge. The numbers behind it are in the Engineer view.
import { alarmSentence, headline, mainReason, PLAIN_ACTION, plainSignal, shownReading, type Light } from "../plain";
import { formatDuration, type AlarmEvent, type LatchedAlarm } from "../stream";
import type { Status, Telemetry } from "../types";
import type { Connection } from "../useEdgeGuard";
import { GLOSSARY, Help } from "./Help";
import { ToneLabel, type Tone } from "./Tone";

const TONE: Record<Light, Tone> = { green: "ok", amber: "warn", red: "bad", off: "idle" };
const clock = (ts: string) => new Date(ts).toLocaleTimeString([], { hour12: false });

/** The traffic light and the one sentence. Holds the Acknowledge button while an alarm is latched. */
export function StatusHero({ latest, latched, live, now, onAck }: {
  latest: Telemetry | null;
  latched: LatchedAlarm | null;
  live: boolean;
  now: number;
  onAck: () => void;
}) {
  const h = headline(latest, latched, live);
  return (
    <section className={`card hero hero-${h.light}`} aria-live="polite">
      <div className="lights" aria-hidden="true">
        {(["red", "amber", "green"] as const).map((c) => (
          <span key={c} className={`lamp lamp-${c}${h.light === c ? " on" : ""}`} />
        ))}
      </div>
      <div className="hero-body">
        <div className="hero-word"><ToneLabel tone={TONE[h.light]}>{h.word}</ToneLabel></div>
        <p className="hero-sentence">{h.sentence}</p>
        {latched && (
          <div className="hero-latch">
            <span className="num">
              {latched.active
                ? <>Alarm since {clock(latched.startTs)} ({formatDuration(now - Date.parse(latched.startTs))})</>
                : <>Alarm {clock(latched.startTs)}–{clock(latched.endTs!)}; readings are back to normal</>}
            </span>
            {latched.acknowledged
              ? <span className="muted">Acknowledged. The light turns green when the readings are normal again.</span>
              : <span><button className="ack" onClick={onAck}>Acknowledge</button>
                  <Help term="Acknowledge">{GLOSSARY.acknowledge}</Help></span>}
          </div>
        )}
      </div>
    </section>
  );
}

/** What to do, and why, in one sentence each. */
export function WhatToDo({ latest, latched, live }: { latest: Telemetry | null; latched: LatchedAlarm | null; live: boolean }) {
  if (!latest || !live) {
    return <section className="card todo"><h2>What to do</h2><p className="todo-action muted">Nothing to show yet.</p></section>;
  }
  const m = shownReading(latest, latched);
  const light = headline(latest, latched, live).light;
  const reason = mainReason(m);
  return (
    <section className={`card todo todo-${light}`}>
      <h2>What to do</h2>
      <p className="todo-action">{PLAIN_ACTION[m.action.code] ?? m.action.text}</p>
      <p className="todo-reason">
        <strong>Why: </strong>
        {reason ?? (m.state === "OK" ? "all readings are in their usual range." : "no single reading stands out.")}
      </p>
      {latched && <p className="muted small">From the alarm's worst reading at {clock(m.ts)}.</p>}
    </section>
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
  const zone = risk == null ? "idle" : risk >= t ? "bad" : risk >= t / 2 ? "warn" : "ok";
  return (
    <section className="card gauge-card">
      <h2>Failure risk now <Help term="Risk">{GLOSSARY.risk}</Help></h2>
      <svg viewBox="0 0 200 116" className="gauge" role="img"
           aria-label={risk != null ? `Risk ${Math.round(risk * 100)} percent, alarm at ${Math.round(t * 100)} percent` : "No risk value yet"}>
        <path d={arc(0, frac(t / 2))} className="g-ok" />
        <path d={arc(frac(t / 2), frac(t))} className="g-warn" />
        <path d={arc(frac(t), 1)} className="g-bad" />
        {needle && <line x1="100" y1="100" x2={needle.x} y2={needle.y} className="g-needle" />}
        <circle cx="100" cy="100" r="6" className="g-hub" />
      </svg>
      <div className={`gauge-value tone-${zone}`}>{risk != null ? `${Math.round(risk * 100)} %` : "–"}</div>
      <p className="muted center">
        Alarm at {Math.round(t * 100)} % <Help term="Alarm threshold">{GLOSSARY.threshold(threshold, costRatio)}</Help>
      </p>
    </section>
  );
}

/** Drift and connection problems, in plain words. Nothing is shown while all is well. */
export function OperatorNotices({ latest, status, connection }: { latest: Telemetry | null; status: Status | null; connection: Connection }) {
  const up = status?.uplink;
  const notices = [];
  if (connection !== "connected") {
    notices.push(<div key="screen" className="banner banner-bad" role="alert">
      <strong>This screen lost its connection.</strong> Reconnecting… The machine itself keeps running and saving data.
    </div>);
  } else if (up && up.state !== "up") {
    notices.push(<div key="uplink" className="banner banner-warn" role="alert">
      <strong>Connection to plant IT is down.</strong> Nothing is lost: {up.buffer_backlog.toLocaleString("en-US")} readings
      are stored on the edge device <Help term="Buffer">{GLOSSARY.buffer}</Help> and will be sent automatically.
    </div>);
  } else if (up && up.buffer_backlog > 10) {
    notices.push(<div key="drain" className="banner banner-ok">
      <strong>Connection is back.</strong> Sending {up.buffer_backlog.toLocaleString("en-US")} stored readings, in order…
    </div>);
  }
  if (latest?.drift_warning) {
    const what = latest.drift_signals.map(plainSignal).join(", ") || "some readings";
    notices.push(<div key="drift" className="banner banner-warn" role="alert">
      <strong>Unusual conditions <Help term="Drift">{GLOSSARY.drift}</Help></strong> The {what} {latest.drift_signals.length > 1 ? "are" : "is"} outside
      what the model learned from. Its prediction may be wrong: check the machine yourself.
    </div>);
  }
  return notices.length ? <div className="notices">{notices}</div> : null;
}

/** Alarm history as short sentences, newest first. */
export function AlarmStory({ alarms }: { alarms: AlarmEvent[] }) {
  return (
    <section className="card">
      <h2>Recent alarms</h2>
      {alarms.length === 0 ? <p className="muted">No alarms yet.</p> : (
        <ul className="story">
          {alarms.slice(0, 8).map((a) => (
            <li key={`${a.seq}-${a.ts}`} className={a.endTs ? "" : "story-active"}>
              {alarmSentence(a, a.endTs ? formatDuration(Date.parse(a.endTs) - Date.parse(a.ts)) : null)}
              {a.buffered && <span className="tag">delivered late</span>}
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">"Replay check" compares with what really happened in the recorded data.</p>
    </section>
  );
}
