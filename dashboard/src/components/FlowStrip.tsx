// Data-flow strip: PLC -> Edge app -> MQTT uplink -> Dashboard, each hop with its live state and
// latency. Health comes from the 1 s status heartbeat, which is NOT buffered, so the strip keeps
// updating during an uplink outage while the telemetry itself waits in the edge app's buffer.
import { int, ms } from "../format";
import type { Connection } from "../useEdgeGuard";
import type { Status } from "../types";
import { ToneLabel, type Tone } from "./Tone";

const DRAINING_MIN = 10;

interface Hop {
  name: string;
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
    name: "PLC",
    tone: !edgeKnown ? "idle" : plc?.connected ? "ok" : "bad",
    state: !edgeKnown ? "unknown" : plc?.connected ? "connected" : "no data",
    latency: ms(plc?.io_ms),
    latencyLabel: "OPC UA read + write",
    detail: plc ? `seq ${plc.last_seq ?? "–"} · ${plc.cycle_ms ?? "–"} ms cycle · ${plc.missed_cycles} missed` : "–",
  };

  const edgeHop: Hop = {
    name: "Edge app",
    tone: edgeKnown ? "ok" : status?.state === "offline" ? "bad" : "idle",
    state: edgeKnown ? "running" : status?.state === "offline" ? "offline" : online ? "no heartbeat" : "unknown",
    latency: ms(status?.edge?.inference_ms),
    latencyLabel: "inference + explanation",
    detail: status ? `model ${status.model_version}` : "–",
  };

  let mqttHop: Hop;
  if (!online) {
    mqttHop = { name: "MQTT uplink", tone: "bad", state: "broker unreachable", latency: "–",
                latencyLabel: "publish → ack", detail: "seen from the dashboard" };
  } else if (!edgeKnown || !up) {
    mqttHop = { name: "MQTT uplink", tone: "idle", state: "unknown", latency: "–",
                latencyLabel: "publish → ack", detail: "–" };
  } else if (up.state === "up") {
    // A backlog of 1-2 is normal (the message waiting for its PUBACK); more means catching up.
    const draining = up.buffer_backlog > DRAINING_MIN;
    mqttHop = { name: "MQTT uplink", tone: draining ? "warn" : "ok",
                state: draining ? `draining ${int(up.buffer_backlog)}` : "up",
                latency: ms(up.ack_ms), latencyLabel: "publish → ack",
                detail: `${int(up.buffer_dropped)} dropped (buffer full)` };
  } else {
    const simulated = up.state === "simulated_down";
    mqttHop = { name: "MQTT uplink", tone: "bad",
                state: simulated ? `outage (simulated) · ${Math.ceil(up.outage_remaining_s)} s` : "down",
                latency: `${int(up.buffer_backlog)} buffered`, latencyLabel: "stored in the edge app",
                detail: `max ${int(up.buffer_max)} · ${int(up.buffer_dropped)} dropped` };
  }

  const dashHop: Hop = {
    name: "Dashboard",
    tone: online ? "ok" : "bad",
    state: connection,
    latency: online ? ms(liveAgeMs) : "–",
    latencyLabel: "edge → browser (live)",
    detail: `${int(received)} msgs · ${int(duplicates)} duplicates dropped`,
  };

  const hops = [plcHop, edgeHop, mqttHop, dashHop];
  return (
    <section className="flow" aria-label="Data flow">
      {hops.map((h, i) => (
        <div className="flow-item" key={h.name}>
          <div className={`flow-box flow-${h.tone}`}>
            <div className="flow-name">{h.name}</div>
            <ToneLabel tone={h.tone}>{h.state}</ToneLabel>
            <div className="flow-latency">{h.latency}</div>
            <div className="muted small">{h.latencyLabel}</div>
            <div className="muted small">{h.detail}</div>
          </div>
          {i < hops.length - 1 && <div className="flow-arrow" aria-hidden="true">→</div>}
        </div>
      ))}
    </section>
  );
}
