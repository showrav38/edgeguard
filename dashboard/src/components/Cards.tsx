// The two cards a technician reads first: machine state, and what to do now (with the reasons).
import { FAILURE_TYPES, pct, SIGNALS, signalValue } from "../format";
import { formatDuration, type LatchedAlarm } from "../stream";
import type { Telemetry } from "../types";
import { GLOSSARY, Help } from "./Help";
import { ToneLabel, type Tone } from "./Tone";

const STATE_TONE: Record<string, Tone> = { OK: "ok", WARNING: "warn", ALARM: "bad" };

const clock = (ts: string) => new Date(ts).toLocaleTimeString([], { hour12: false });

/** Latched alarm: when it started, how long it has been (or was) active, and the Acknowledge button. */
function AlarmLatch({ latched, now, onAck }: { latched: LatchedAlarm; now: number; onAck: () => void }) {
  const start = Date.parse(latched.startTs);
  return (
    <div className="latch">
      <p className="latch-time num">
        {latched.active
          ? <>Active since {clock(latched.startTs)} · <strong>{formatDuration(now - start)}</strong></>
          : <>Was active {clock(latched.startTs)}–{clock(latched.endTs!)} ({formatDuration(Date.parse(latched.endTs!) - start)}).
              Readings are back to normal.</>}
      </p>
      {latched.acknowledged
        ? <p className="muted small">Acknowledged. Clears when the readings leave ALARM.</p>
        : <button className="ack" onClick={onAck}>Acknowledge</button>}
    </div>
  );
}

export function StatusCard({ m, latched, now, onAck, threshold, costRatio }: {
  m: Telemetry | null;
  latched: LatchedAlarm | null;
  now: number;
  onAck: () => void;
  threshold?: number;
  costRatio?: number;
}) {
  if (!m) return <section className="card status-card"><h2>Machine 1</h2><p className="muted">Waiting for data…</p></section>;
  // A latched alarm wins over the current reading: ALARM stays until it is acknowledged.
  const state = latched ? "ALARM" : m.state;
  const tone = STATE_TONE[state] ?? "idle";
  const label = latched && !latched.active ? "ALARM (cleared, not acknowledged)"
    : latched?.acknowledged ? "ALARM · acknowledged" : state;
  return (
    <section className={`card status-card status-${tone}`}>
      <h2>Machine 1</h2>
      <div className="big-state"><ToneLabel tone={tone}>{label}</ToneLabel></div>
      {latched && <AlarmLatch latched={latched} now={now} onAck={onAck} />}
      <dl className="facts">
        <div><dt>Current failure risk <Help term="Risk">{GLOSSARY.risk}</Help></dt><dd className="num">{pct(m.risk)}</dd></div>
        <div><dt>Alarm threshold <Help term="Alarm threshold">{GLOSSARY.threshold(threshold, costRatio)}</Help></dt><dd className="num">{threshold != null ? pct(threshold) : "–"}</dd></div>
        <div><dt>Most likely failure</dt>
          <dd>{FAILURE_TYPES[latched?.peak.failure_type ?? m.failure_type] ?? m.failure_type}</dd></div>
        <div><dt>Reading</dt><dd className="num">seq {m.seq}{m.buffered ? " (buffered)" : ""}</dd></div>
      </dl>
      {costRatio != null && (
        <p className="muted small">
          Threshold chosen by cost: a missed failure counts {costRatio}× a false alarm.
        </p>
      )}
    </section>
  );
}

export function ActionCard({ latest, latched }: { latest: Telemetry | null; latched: LatchedAlarm | null }) {
  if (!latest) return <section className="card action-card"><h2>What to do now</h2><p className="muted">–</p></section>;
  // While an alarm is latched, show ITS action (from its highest-risk reading), not the latest reading's.
  const m = latched ? latched.peak : latest;
  const tone = latched ? "bad" : STATE_TONE[m.state] ?? "idle";
  const maxEffect = Math.max(0.01, ...m.top_factors.map((f) => Math.abs(f.effect)));
  const pushing = m.top_factors.some((f) => f.effect >= 0.005); // at least 0.5 risk points
  return (
    <section className={`card action-card status-${tone}`}>
      <h2>What to do now</h2>
      <p className="action-text">{m.action.text}</p>
      <h3>Why: the 2 signals that pushed the risk most</h3>
      {!pushing && <p className="muted">No signal is raising the risk right now.</p>}
      {pushing && <ul className="factors">
        {m.top_factors.slice(0, 2).map((f) => {
          const value = (m.signals as unknown as Record<string, number | string>)[f.signal];
          return (
            <li key={f.signal}>
              <div className="factor-head">
                <span>{SIGNALS[f.signal]?.label ?? f.signal}</span>
                <span className="num">{value != null ? signalValue(f.signal, value) : ""}</span>
              </div>
              <div className="factor-bar" aria-hidden="true">
                <div style={{ width: `${(Math.max(0, f.effect) / maxEffect) * 100}%` }} />
              </div>
              <div className="muted small num">
                {f.effect >= 0 ? "+" : "−"}{Math.abs(f.effect * 100).toFixed(1)} risk points vs. a typical value
              </div>
            </li>
          );
        })}
      </ul>}
      {latched && <p className="muted small">From the alarm's highest-risk reading (seq {m.seq}, risk {pct(m.risk)}).</p>}
      <p className="muted small">
        Method: each signal is set to its training median; the drop in risk is its effect (one signal at a time).
      </p>
    </section>
  );
}

export function DriftBanner({ m }: { m: Telemetry | null }) {
  if (!m?.drift_warning) return null;
  const names = m.drift_signals.map((s) => SIGNALS[s]?.label ?? s).join(", ");
  return (
    <div className="banner banner-warn" role="alert">
      <span><ToneLabel tone="warn">Drift</ToneLabel> <Help term="Drift">{GLOSSARY.drift}</Help></span>
      <span>
        Live data has moved away from the training data ({names}; shift {m.drift_score?.toFixed(1)} std).
        Predictions may be unreliable.
      </span>
    </div>
  );
}
