// MQTT connection to the broker (websockets) and the dashboard state built from the messages.
import mqtt, { type MqttClient } from "mqtt";
import { useCallback, useEffect, useRef, useState } from "react";
import { addTelemetry, emptyView, SeqDeduper, type View } from "./stream";
import type { Command, Status, Telemetry } from "./types";

// Default: the broker runs on the same host that serves the dashboard.
const MQTT_URL = import.meta.env.VITE_MQTT_URL ?? `ws://${window.location.hostname}:9001`;
const TOPIC_TELEMETRY = "edgeguard/machine1/telemetry";
const TOPIC_STATUS = "edgeguard/machine1/status";
const TOPIC_CMD = "edgeguard/machine1/cmd";
const FLUSH_MS = 200; // apply queued telemetry to the view at most 5x per second

export type Connection = "connecting" | "connected" | "reconnecting" | "offline";

export function useEdgeGuard() {
  const [view, setView] = useState<View>(emptyView);
  const [status, setStatus] = useState<Status | null>(null);
  const [statusAt, setStatusAt] = useState<number | null>(null); // when the last heartbeat arrived
  const [connection, setConnection] = useState<Connection>("connecting");
  const [duplicates, setDuplicates] = useState(0);
  const [received, setReceived] = useState(0);

  const clientRef = useRef<MqttClient | null>(null);
  const deduper = useRef(new SeqDeduper());
  // Messages wait here until the next flush. After an outage the edge app drains its buffer at
  // ~150 msg/s; re-rendering the charts for every single message would freeze the page.
  const pending = useRef<{ m: Telemetry; at: number }[]>([]);

  useEffect(() => {
    const client = mqtt.connect(MQTT_URL, {
      clientId: `edgeguard-dashboard-${Math.random().toString(16).slice(2, 8)}`,
      reconnectPeriod: 1000,
      connectTimeout: 5000,
    });
    clientRef.current = client;

    client.on("connect", () => {
      setConnection("connected");
      client.subscribe([TOPIC_TELEMETRY, TOPIC_STATUS], { qos: 1 });
    });
    client.on("reconnect", () => setConnection("reconnecting"));
    client.on("close", () => setConnection("offline"));

    client.on("message", (topic, payload) => {
      let data: unknown;
      try {
        data = JSON.parse(payload.toString());
      } catch {
        return; // not JSON: ignore
      }
      if (topic === TOPIC_STATUS) {
        setStatus(data as Status);
        setStatusAt(Date.now());
        return;
      }
      const m = data as Telemetry;
      if (!deduper.current.accept(m.seq, m.ts)) {
        setDuplicates(deduper.current.dropped);
        return;
      }
      pending.current.push({ m, at: Date.now() });
    });

    const flush = setInterval(() => {
      const batch = pending.current;
      if (batch.length === 0) return;
      pending.current = [];
      setView((v) => batch.reduce((acc, { m, at }) => addTelemetry(acc, m, at), v));
      setReceived((n) => n + batch.length);
    }, FLUSH_MS);

    return () => {
      clearInterval(flush);
      client.end(true);
    };
  }, []);

  const send = useCallback((cmd: Command) => {
    clientRef.current?.publish(TOPIC_CMD, JSON.stringify(cmd), { qos: 1 });
  }, []);

  return { view, status, statusAt, connection, duplicates, received, send, url: MQTT_URL };
}

/** Re-render every `ms` so "seconds ago" and heartbeat timeouts stay current. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
