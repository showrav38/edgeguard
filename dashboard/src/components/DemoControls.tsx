// Demo controls. They publish to edgeguard/machine1/cmd; the edge app validates each command and
// changes the SIMULATED PLC (drift, replay speed) or simulates an uplink outage inside itself.
// Nothing here touches a real machine, and the UI says so. Buttons keep their place and width.
import { IxButton, IxToggleButton } from "@siemens/ix-react";
import type { Command, Status } from "../types";
import { Card, ICON } from "./ui";

const OUTAGE_S = 20;
// 1x = one reading per second (the PLC simulator default); one tool life is ~84 readings.
const SPEEDS = [
  { label: "1x", cycleMs: 1000 },
  { label: "2x", cycleMs: 500 },
  { label: "5x", cycleMs: 200 },
  { label: "10x", cycleMs: 100 },
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
    <Card box="demo" title="Demo controls (simulated machine)" icon={ICON.tour} className="demo">
      <p className="small muted">
        These act on the simulated PLC and simulate an uplink outage inside the edge app. The dashboard
        stays connected so you can watch the buffer fill and drain.
      </p>
      <div className="control-row">
        <span>Drift mode</span>
        <IxButton data-box="demo-drift" className="btn-demo" variant="subtle-secondary" disabled={!enabled || drift === null}
                  onClick={() => send({ cmd: "drift", on: !drift })}>
          {drift ? "Turn drift off" : "Turn drift on"}
        </IxButton>
      </div>
      <div className="control-row">
        <span>Uplink</span>
        <IxButton data-box="demo-outage" className="btn-demo" variant="subtle-secondary" disabled={!enabled || outage}
                  onClick={() => send({ cmd: "outage", seconds: OUTAGE_S })}>
          {outage ? `Outage running, ${remaining} s` : `Simulate outage ${OUTAGE_S} s`}
        </IxButton>
      </div>
      <div className="control-row stacked">
        <span>Replay speed</span>
        <div className="speeds" role="group" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <IxToggleButton key={s.cycleMs} data-box={`demo-speed-${s.label}`} variant="subtle-secondary" disabled={!enabled}
                            pressed={cycleMs === s.cycleMs} title={`one reading every ${s.cycleMs} ms`}
                            onClick={() => send({ cmd: "speed", cycle_ms: s.cycleMs })}>
              {s.label}
            </IxToggleButton>
          ))}
        </div>
      </div>
      <p className="small error one-line">{last && !last.ok ? `Command rejected: ${last.error}` : " "}</p>
    </Card>
  );
}
