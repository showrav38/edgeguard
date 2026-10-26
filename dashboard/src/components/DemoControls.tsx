// Demo controls. They publish to edgeguard/machine1/cmd; the edge app validates each command and
// changes the SIMULATED PLC (drift, replay speed) or simulates an uplink outage inside itself.
// Nothing here touches a real machine, and the UI says so.
import type { Command, Status } from "../types";

const OUTAGE_S = 20;
const SPEEDS = [
  { label: "0.5×", cycleMs: 400 },
  { label: "1×", cycleMs: 200 },
  { label: "2×", cycleMs: 100 },
  { label: "4×", cycleMs: 50 },
];

export function DemoControls({ status, enabled, send }: {
  status: Status | null;
  enabled: boolean;
  send: (c: Command) => void;
}) {
  const drift = status?.plc?.drift_mode ?? null;
  const cycleMs = status?.plc?.cycle_ms ?? null;
  const outage = status?.uplink?.state === "simulated_down";
  const remaining = Math.ceil(status?.uplink?.outage_remaining_s ?? 0);
  const last = status?.last_command;

  return (
    <section className="card demo">
      <h2>Demo controls <span className="tag tag-sim">simulated</span></h2>
      <p className="muted small">
        These act on the simulated PLC and simulate an uplink outage inside the edge app. The dashboard
        stays connected so you can watch the buffer fill and drain.
      </p>

      <div className="control-row">
        <span>Drift mode</span>
        <button disabled={!enabled || drift === null} aria-pressed={drift === true}
                onClick={() => send({ cmd: "drift", on: !drift })}>
          {drift ? "On — turn off" : "Off — turn on"}
        </button>
      </div>

      <div className="control-row">
        <span>Uplink</span>
        <button disabled={!enabled || outage} onClick={() => send({ cmd: "outage", seconds: OUTAGE_S })}>
          {outage ? `Outage running… ${remaining} s` : `Simulate uplink outage ${OUTAGE_S} s`}
        </button>
      </div>

      <div className="control-row">
        <span>Replay speed</span>
        <div className="segmented" role="group" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <button key={s.cycleMs} disabled={!enabled} aria-pressed={cycleMs === s.cycleMs}
                    onClick={() => send({ cmd: "speed", cycle_ms: s.cycleMs })}
                    title={`one reading every ${s.cycleMs} ms`}>
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {last && !last.ok && <p className="error small">Command rejected: {last.error}</p>}
    </section>
  );
}
