// MQTT connection to the broker (websockets) and the dashboard state built from the messages.
//
// Calm updates: messages are collected as they arrive, but the SCREEN changes at most once per
// second (one "tick" applies all new telemetry, the latest status and the clock in one render).
// Pause freezes the screen; messages keep being collected and Resume applies them all at once.
import mqtt, { type MqttClient } from "mqtt";
import { useCallback, useEffect, useRef, useState } from "react";
import { acknowledge, addTelemetry, emptyView, SeqDeduper, type View } from "./stream";
import type { Command, RobotStatus, Status, Telemetry } from "./types";

// Default: the broker runs on the same host that serves the dashboard.
const MQTT_URL = import.meta.env.VITE_MQTT_URL ?? `ws://${window.location.hostname}:9001`;
const TOPIC_TELEMETRY = "edgeguard/machine1/telemetry";
const TOPIC_STATUS = "edgeguard/machine1/status";
const TOPIC_CMD = "edgeguard/machine1/cmd";
const TOPIC_ACK = "edgeguard/machine1/ack"; // read by the ROS 2 node cell_guard (releases the robot interlock)
const TOPIC_ROBOT = "edgeguard/robot_cell/status"; // from the ROS 2 node robot_sim (retained)
const TICK_MS = 1000; // the screen updates at most once per second

export type Connection = "connecting" | "connected" | "reconnecting" | "offline";

/** What one render shows. Frozen as a whole while paused. */
export interface Screen {
  view: View;
  status: Status | null;
  statusAt: number | null; // when the last heartbeat arrived
  robot: RobotStatus | null;
  connection: Connection;
  received: number;
  duplicates: number;
  now: number;
}

const firstScreen = (): Screen => ({
  view: emptyView, status: null, statusAt: null, robot: null, connection: "connecting", received: 0, duplicates: 0, now: Date.now(),
});

export function useEdgeGuard() {
  const [screen, setScreen] = useState<Screen>(firstScreen);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);

  const clientRef = useRef<MqttClient | null>(null);
  const deduper = useRef(new SeqDeduper());
  // Everything that arrived since the last tick. After an outage the edge app drains its buffer at
  // ~150 msg/s; applying them once per tick keeps the page calm and fast.
  const pending = useRef<{ m: Telemetry; at: number }[]>([]);
  const latest = useRef({ status: null as Status | null, statusAt: null as number | null, robot: null as RobotStatus | null,
                          connection: "connecting" as Connection, duplicates: 0 });
  // The live state keeps advancing while the screen is paused, so Resume can catch up.
  const live = useRef<Screen>(firstScreen());

  useEffect(() => {
    const client = mqtt.connect(MQTT_URL, {
      clientId: `edgeguard-dashboard-${Math.random().toString(16).slice(2, 8)}`,
      reconnectPeriod: 1000,
      connectTimeout: 5000,
    });
    clientRef.current = client;

    client.on("connect", () => {
      latest.current.connection = "connected";
      client.subscribe([TOPIC_TELEMETRY, TOPIC_STATUS, TOPIC_ROBOT], { qos: 1 });
    });
    client.on("reconnect", () => { latest.current.connection = "reconnecting"; });
    client.on("close", () => { latest.current.connection = "offline"; });

    client.on("message", (topic, payload) => {
      let data: unknown;
      try {
        data = JSON.parse(payload.toString());
      } catch {
        return; // not JSON: ignore
      }
      if (topic === TOPIC_STATUS) {
        latest.current.status = data as Status;
        latest.current.statusAt = Date.now();
        return;
      }
      if (topic === TOPIC_ROBOT) {
        latest.current.robot = data as RobotStatus;
        return;
      }
      const m = data as Telemetry;
      if (!deduper.current.accept(m.seq, m.ts)) {
        latest.current.duplicates = deduper.current.dropped;
        return;
      }
      pending.current.push({ m, at: Date.now() });
    });

    const tick = setInterval(() => {
      const batch = pending.current;
      pending.current = [];
      const prev = live.current;
      live.current = {
        ...prev,
        ...latest.current,
        view: batch.reduce((acc, { m, at }) => addTelemetry(acc, m, at), prev.view),
        received: prev.received + batch.length,
        now: Date.now(),
      };
      if (!pausedRef.current) setScreen(live.current);
    }, TICK_MS);

    return () => {
      clearInterval(tick);
      client.end(true);
    };
  }, []);

  const send = useCallback((cmd: Command) => {
    clientRef.current?.publish(TOPIC_CMD, JSON.stringify(cmd), { qos: 1 });
  }, []);

  // The dashboard's alarm latch is local to this browser (see docs/decisions.md D-22). Shown at once: it is the
  // user's own action. The acknowledge is also published, with the time of the newest reading the operator has
  // seen, so cell_guard can release the robot interlock (D-31).
  const ack = useCallback(() => {
    const seen = live.current.view.latest;
    clientRef.current?.publish(TOPIC_ACK, JSON.stringify({ ts: seen?.ts ?? null, seq: seen?.seq ?? null }), { qos: 1 });
    live.current = { ...live.current, view: acknowledge(live.current.view, Date.now()) };
    setScreen(live.current);
  }, []);

  const togglePause = useCallback(() => {
    pausedRef.current = !pausedRef.current;
    setPaused(pausedRef.current);
    if (!pausedRef.current) setScreen(live.current); // Resume: catch up at once
  }, []);

  return { screen, paused, togglePause, send, ack, url: MQTT_URL };
}
