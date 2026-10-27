// Data-flow strip: PLC -> Edge app -> MQTT uplink -> Dashboard, each hop with its live state and
// latency. Health comes from the 1 s status heartbeat, which is NOT buffered, so the strip keeps
// updating during an uplink outage while the telemetry itself waits in the edge app's buffer.
// Connection problems are amber, not red: red is reserved for an active machine alarm.
import { IxIcon } from "@siemens/ix-react";
import { int, ms } from "../format";
import type { Connection } from "../useEdgeGuard";
import type { Status } from "../types";
import { GLOSSARY, Help, ICON, StateLabel, type Tone } from "./ui";

const DRAINING_MIN = 10;

interface Hop {
  box: string;
  name: string;
  icon: string;
  tone: Tone;
  state: string;
  latency: string;
  latencyLabel: string;
  detail: string;
}

interface Props {
  status: Status | null;
  heartbeatFresh: boolean;
  connection: Connection;
  liveAgeMs: number | null;
  received: number;
  duplicates: number;
}

export function FlowStrip({ status, heartbeatFresh, connection, liveAgeMs, received, duplicates }: Props) {
  const online = connection === "connected";
  const edgeKnown = online && heartbeatFresh && status?.state === "running";
  const plc = status?.plc;
  const up = status?.uplink;

  const plcHop: Hop = {
    box: "flow-plc", name: "PLC", icon: ICON.plc,
    tone: !edgeKnown ? "idle" : plc?.connected ? "ok" : "warn",
    state: !edgeKnown ? "unknown" : plc?.connected ? "connected" : "no data",
    latency: ms(plc?.io_ms),
    latencyLabel: "OPC UA read + write",
    detail: plc ? `seq ${plc.last_seq ?? "–"} · ${plc.cycle_ms ?? "–"} ms cycle · ${plc.missed_cycles} missed` : "–",
  };

  const edgeHop: Hop = {
    box: "flow-edge", name: "Edge app", icon: ICON.edge,
    tone: edgeKnown ? "ok" : status?.state === "offline" ? "warn" : "idle",
    state: edgeKnown ? "running" : status?.state === "offline" ? "offline" : online ? "no heartbeat" : "unknown",
    latency: ms(status?.edge?.inference_ms),
    latencyLabel: "inference + explanation",
    detail: status ? `model ${status.model_version}` : "–",
  };

  let mqttHop: Hop = { box: "flow-uplink", name: "MQTT uplink", icon: ICON.uplink, tone: "idle", state: "unknown",
                       latency: "–", latencyLabel: "publish → ack", detail: "–" };
  if (!online) {
    mqttHop = { ...mqttHop, tone: "warn", state: "broker unreachable", detail: "seen from the dashboard" };
  } else if (edgeKnown && up && up.state === "up") {
    // A backlog of 1-2 is normal (the message waiting for its PUBACK); more means catching up.
    const draining = up.buffer_backlog > DRAINING_MIN;
    mqttHop = { ...mqttHop, tone: draining ? "info" : "ok", state: draining ? `sending ${int(up.buffer_backlog)} stored` : "up",
                latency: ms(up.ack_ms), detail: `${int(up.buffer_dropped)} dropped (buffer full)` };
  } else if (edgeKnown && up) {
    const simulated = up.state === "simulated_down";
    mqttHop = { ...mqttHop, tone: "warn",
                state: simulated ? `outage (simulated), ${Math.ceil(up.outage_remaining_s)} s` : "down",
                latency: `${int(up.buffer_backlog)} stored`, latencyLabel: "in the edge buffer",
                detail: `max ${int(up.buffer_max)} · ${int(up.buffer_dropped)} dropped` };
  }

  const dashHop: Hop = {
    box: "flow-screen", name: "Dashboard", icon: ICON.screen,
    tone: online ? "ok" : "warn",
    state: connection,
    latency: online ? ms(liveAgeMs) : "–",
    latencyLabel: "edge → browser (live)",
    detail: `${int(received)} msgs · ${int(duplicates)} duplicates dropped`,
  };

  const hops = [plcHop, edgeHop, mqttHop, dashHop];
  return (
    <section className="flow" aria-label="Data flow">
      {hops.map((h, i) => (
        <div className={`card flow-box card-${h.tone}`} data-box={h.box} key={h.name}>
          <div className="flow-name">
            <IxIcon name={h.icon} size="16" aria-hidden="true" /> {h.name}
            {i === 2 && <Help term="Buffer" box="help-buffer">{GLOSSARY.buffer}</Help>}
            {i === 0 && <Help term="Latency" box="help-latency">{GLOSSARY.latency}</Help>}
          </div>
          <div className="one-line"><StateLabel tone={h.tone}>{h.state}</StateLabel></div>
          <div className="flow-latency num">{h.latency}</div>
          <div className="small muted one-line">{h.latencyLabel}</div>
          <div className="small muted num one-line">{h.detail}</div>
        </div>
      ))}
    </section>
  );
}
