// Engineer view cards: machine state (with Acknowledge), what to do with the 2 top factors, and data quality (drift).
// Fixed sizes: the content changes with the state, the card and its buttons never move.
import { IxButton } from "@siemens/ix-react";
import { FAILURE_TYPES, pct, SIGNALS, signalValue } from "../format";
import type { LatchedAlarm, View } from "../stream";
import type { Telemetry } from "../types";
import { canAck, latchLine } from "./Operator";
import { Card, GLOSSARY, Help, ICON, StateLabel, Waiting, type Tone } from "./ui";

const STATE_TONE: Record<string, Tone> = { OK: "ok", WARNING: "warn", ALARM: "bad" };

const clock = (ts: string) => new Date(ts).toLocaleTimeString([], { hour12: false });

/**
 * Engineer status label. Red only for an active alarm (risk above the threshold now). An alarm that is over
 * but not acknowledged, and a repeat during the cool-down after Acknowledge, are amber.
 */
export function engineerState(m: Telemetry | null, latched: LatchedAlarm | null): { tone: Tone; label: string } {
  if (!m) return { tone: "idle", label: "No data" };
  if (latched?.active) return { tone: "bad", label: latched.acknowledged ? "ALARM, acknowledged" : "ALARM" };
  if (latched) return { tone: "warn", label: `Alarm at ${clock(latched.startTs)} needs your check` };
  if (m.state === "ALARM") return { tone: "warn", label: "ALARM again, in cool-down" };
  return { tone: STATE_TONE[m.state] ?? "idle", label: m.state };
}

export function StatusCard({ view, live, paused, now, onAck, threshold, costRatio }: {
  view: View;
  live: boolean;
  paused: boolean;
  now: number;
  onAck: () => void;
  threshold?: number;
  costRatio?: number;
}) {
  const { latest: m, latched } = view;
  // A latched alarm wins over the current reading: it stays until it is acknowledged.
  const { tone, label } = engineerState(m, latched);
  const row = (dt: string, dd: string, help?: React.ReactNode) => (
    <div><dt>{dt}{help}</dt><dd className="num">{m ? dd : "–"}</dd></div>
  );
  return (
    <Card box="status-engineer" title="Machine 1" icon={ICON.screen} className="eng-card" tone={tone}>
      <div className="eng-state"><StateLabel tone={tone}>{label}</StateLabel></div>
      <p className="small muted num latch-line">{m ? latchLine(view, now) : "Waiting for machine data..."}</p>
      <dl className="facts">
        {row("Failure risk", m ? pct(m.risk) : "", <Help term="Risk" box="help-risk-eng">{GLOSSARY.risk}</Help>)}
        {row("Alarm threshold", threshold != null ? pct(threshold) : "–",
             <Help term="Alarm threshold" box="help-threshold-eng">{GLOSSARY.threshold(threshold, costRatio)}</Help>)}
        {row("Most likely failure", m ? FAILURE_TYPES[latched?.peak.failure_type ?? m.failure_type] ?? m.failure_type : "")}
        {row("Reading", m ? `seq ${m.seq}${m.buffered ? " (buffered)" : ""}` : "")}
      </dl>
      <div className="card-actions">
        <IxButton data-box="ack-engineer" className="btn-ack" variant={canAck(latched, live, paused) ? "primary" : "subtle-secondary"}
                  icon={ICON.ack} disabled={!canAck(latched, live, paused)} onClick={onAck}>Acknowledge</IxButton>
      </div>
    </Card>
  );
}

export function ActionCard({ latest, latched }: { latest: Telemetry | null; latched: LatchedAlarm | null }) {
  if (!latest) return <Card box="action-engineer" title="What to do now" icon={ICON.todo} className="eng-card"><Waiting /></Card>;
  // While an alarm is latched, show ITS action (from its highest-risk reading), not the latest reading's.
  // No status colour here: the status card is the one place for it.
  const m = latched ? latched.peak : latest;
  const maxEffect = Math.max(0.01, ...m.top_factors.map((f) => Math.abs(f.effect)));
  const pushing = m.top_factors.some((f) => f.effect >= 0.005); // at least 0.5 risk points
  // Always 2 factor rows, so the card looks the same with or without factors.
  const factors = [0, 1].map((i) => (pushing ? m.top_factors[i] : undefined));
  return (
    <Card box="action-engineer" title="What to do now" icon={ICON.todo} className="eng-card">
      <p className="action-text two-lines">{m.action.text}</p>
      <h3 className="label">Why: the 2 signals that pushed the risk most</h3>
      <ul className="factors">
        {factors.map((f, i) => {
          const value = f ? (m.signals as unknown as Record<string, number | string>)[f.signal] : undefined;
          return (
            <li key={i}>
              <div className="factor-head">
                <span>{f ? SIGNALS[f.signal]?.label ?? f.signal : i === 0 ? "No signal is raising the risk" : " "}</span>
                <span className="num">{f && value != null ? signalValue(f.signal, value) : ""}</span>
              </div>
              <div className="factor-bar" aria-hidden="true">
                <div style={{ width: f ? `${(Math.max(0, f.effect) / maxEffect) * 100}%` : "0%" }} />
              </div>
              <div className="small muted num">
                {f ? `${f.effect >= 0 ? "+" : "−"}${Math.abs(f.effect * 100).toFixed(1)} risk points vs. a typical value` : " "}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="small muted">Method: each signal is set to its training median; the drop in risk is its effect.</p>
    </Card>
  );
}

/** Drift, always shown (replaces a banner that appeared and pushed the page down). */
export function DataQuality({ m }: { m: Telemetry | null }) {
  const tone: Tone = !m ? "idle" : m.drift_warning ? "warn" : m.drift_score == null ? "idle" : "ok";
  const label = !m ? "No data" : m.drift_warning ? "Drift: predictions may be unreliable"
    : m.drift_score == null ? "Collecting readings" : "Live data looks like the training data";
  const names = m?.drift_signals.map((s) => SIGNALS[s]?.label ?? s).join(", ") || "none";
  return (
    <Card box="drift-engineer" title="Data quality" icon={ICON.chart} className="eng-card" tone={m?.drift_warning ? "warn" : undefined}
          help={<Help term="Drift" box="help-drift-eng">{GLOSSARY.drift}</Help>}>
      <div className="eng-state"><StateLabel tone={tone}>{label}</StateLabel></div>
      <dl className="facts">
        <div><dt>Drift score (window mean shift)</dt><dd className="num">{m?.drift_score != null ? `${m.drift_score.toFixed(2)} std` : "–"}</dd></div>
        <div className="tall"><dt>Signals outside training</dt><dd>{m ? names : "–"}</dd></div>
        <div><dt>Inference + explanation</dt><dd className="num">{m ? `${m.inference_ms.toFixed(2)} ms` : "–"}</dd></div>
        <div><dt>Missed PLC cycles</dt><dd className="num">{m ? m.missed_cycles.toLocaleString("en-US") : "–"}</dd></div>
      </dl>
    </Card>
  );
}
