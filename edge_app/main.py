"""EdgeGuard edge app: PLC (OPC UA) -> ONNX -> explanation -> action -> PLC write-back + MQTT.

One loop iteration per PLC cycle:
  1. wait for a new Seq, read the full row
  2. risk + failure type (ONNX), top-2 factors (occlusion), state + action text
  3. write results back to the PLC (one write request, LastSeqProcessed last)
  4. publish telemetry over MQTT, through the store-and-forward buffer (M4)
Write-back comes before MQTT: the PLC is the time-critical consumer, and it keeps getting
results while the MQTT/IT side is down. A drift monitor (M4) flags readings unlike the training data.

Next to the loop (M5):
  - a status "heartbeat" every second on edgeguard/machine1/status (retained): PLC, edge and uplink
    health + buffer counts. It is published directly, NOT through the outbox, so the dashboard sees
    the backlog grow during a (simulated) outage. Telemetry still goes only through the outbox.
  - demo commands from the dashboard on edgeguard/machine1/cmd (see commands.py).

Run: python -m edge_app.main      Config via environment variables.
"""
import asyncio
import json
import logging
import os
import statistics
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from edge_app import actions, commands, explain
from edge_app.buffer import Outbox, StoreAndForward
from edge_app.drift import DriftMonitor, thresholds
from edge_app.inference import Models
from edge_app.opcua_client import PlcClient

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
# Must be unique per broker: a second client with the same id makes the broker drop the first.
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "edgeguard-edge")
TOPIC_TELEMETRY = os.getenv("MQTT_TOPIC_TELEMETRY", "edgeguard/machine1/telemetry")
TOPIC_STATUS = os.getenv("MQTT_TOPIC_STATUS", "edgeguard/machine1/status")
TOPIC_CMD = os.getenv("MQTT_TOPIC_CMD", "edgeguard/machine1/cmd")
DEMO_COMMANDS = os.getenv("DEMO_COMMANDS", "1") == "1"  # 0 = ignore the command topic
STATUS_INTERVAL_S = float(os.getenv("STATUS_INTERVAL_S", "1.0"))

log = logging.getLogger("edge")


def make_mqtt(models: Models, on_command) -> mqtt.Client:
    """paho-mqtt client running its network loop in a background thread.
    on_command(payload: bytes) is called (in paho's thread) for every message on the command topic."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    status = {"model_version": models.version, "threshold": models.threshold,
              "cost_ratio": models.info["cost_ratio"]}
    # If we die without saying goodbye, the broker publishes this retained "offline" status.
    client.will_set(TOPIC_STATUS, json.dumps({"state": "offline", **status}), qos=1, retain=True)

    def on_connect(c, userdata, flags, reason_code, properties):
        log.info("MQTT connected to %s:%d (%s)", MQTT_HOST, MQTT_PORT, reason_code)
        c.publish(TOPIC_STATUS, json.dumps({"state": "running", **status}), qos=1, retain=True)
        if DEMO_COMMANDS:
            c.subscribe(TOPIC_CMD, qos=1)  # (re)subscribe on every connect: clean session

    client.on_connect = on_connect
    client.on_message = lambda c, userdata, m: on_command(m.payload)
    # Retry every 1-5 s while the broker is down (paho's default backs off up to 2 minutes).
    client.reconnect_delay_set(min_delay=1, max_delay=5)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=10)
    client.loop_start()  # reconnects automatically
    return client


def telemetry(seq, signals, pred, decision, factors, drift, inference_ms, plc_io_ms, ground_truth,
              missed) -> dict:
    return {
        "seq": seq,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "signals": signals,
        "risk": round(pred["risk"], 4),
        "alarm": pred["alarm"],
        "state": decision["state"],
        "failure_type": decision["failure_type"],
        "action": {"code": decision["code"], "text": decision["text"]},
        "top_factors": factors,
        "drift_score": drift["score"],       # None until the drift window is full
        "drift_warning": drift["warning"],
        "drift_signals": drift["signals"],   # signals that moved away from training
        "inference_ms": round(inference_ms, 3),
        "plc_io_ms": round(plc_io_ms, 3),    # OPC UA read of the row + write-back, as seen by the edge
        # "buffered" (stored while the uplink was down), "buffer_backlog" and "buffer_dropped" are
        # set by StoreAndForward
        "ground_truth": ground_truth,
        "missed_cycles": missed,
    }


class Edge:
    """The edge app: holds the connections and the live numbers the status heartbeat reports."""

    def __init__(self):
        self.models = Models()
        log.info("models loaded: version %s, threshold %.2f", self.models.version, self.models.threshold)
        self.commands: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        # paho calls on_command in its own thread; hand the payload over to the asyncio loop safely.
        self.mqtt = make_mqtt(self.models, lambda p: loop.call_soon_threadsafe(self.commands.put_nowait, p))
        self.outbox = Outbox()
        self.sender = StoreAndForward(self.mqtt, self.outbox)
        self.drift_monitor = DriftMonitor(self.models.info["training_stats"], *thresholds(self.models.info))
        self.plc = PlcClient()
        # Live numbers for the status heartbeat (updated by the loop)
        self.last_seq, self.missed, self.last_row_at = None, 0, 0.0
        self.inference_ms = self.plc_io_ms = None
        self.last_command = None

    async def run(self):
        await self.plc.connect()
        queue = await self.plc.subscribe_seq()
        asyncio.create_task(self.status_loop())
        if DEMO_COMMANDS:
            asyncio.create_task(self.command_loop())

        n = 0
        inf_ms: list[float] = []
        while True:
            await queue.get()
            t_read = time.perf_counter()
            seq, signals, ground_truth, drift_mode = await self.plc.read_row()
            read_ms = (time.perf_counter() - t_read) * 1000
            if seq == 0 or (self.last_seq is not None and seq <= self.last_seq):
                continue  # PLC not started yet, or we already handled this row (read was ahead)
            if self.last_seq is not None:
                self.missed += seq - self.last_seq - 1

            t0 = time.perf_counter()
            pred = self.models.predict(signals)
            factors = explain.top_factors(self.models, pred["x"], pred["risk"])
            decision = actions.decide(pred, signals, self.models.threshold)
            drift = self.drift_monitor.update(signals)
            inference_ms = (time.perf_counter() - t0) * 1000

            t_write = time.perf_counter()
            await self.plc.write_outputs({
                "FailureRisk": pred["risk"],
                "Alarm": pred["alarm"],
                "FailureType": decision["failure_type"],
                "ActionCode": decision["code"],
                "DriftWarning": drift["warning"],
                "InferenceMs": inference_ms,
                "ModelVersion": self.models.version,
                "LastSeqProcessed": seq,
            })
            plc_io_ms = read_ms + (time.perf_counter() - t_write) * 1000
            msg = telemetry(seq, signals, pred, decision, factors, drift, inference_ms, plc_io_ms,
                            ground_truth, self.missed)
            self.sender.send(TOPIC_TELEMETRY, msg)  # stored in SQLite first, sent in order by a thread

            self.last_seq, self.last_row_at = seq, time.monotonic()
            self.inference_ms, self.plc_io_ms = inference_ms, plc_io_ms
            n += 1
            inf_ms.append(inference_ms)
            if n % 100 == 0:
                log.info("seq=%d processed=%d missed=%d inference mean %.2f ms (last 100) "
                         "drift=%s backlog=%d dropped=%d", seq, n, self.missed, statistics.mean(inf_ms),
                         drift["score"], msg["buffer_backlog"], msg["buffer_dropped"])
                inf_ms.clear()

    def status(self, demo: dict) -> dict:
        """Health of every hop, for the dashboard's data-flow strip."""
        r = lambda v: None if v is None else round(v, 2)  # noqa: E731
        return {
            "state": "running",
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "model_version": self.models.version,
            "threshold": self.models.threshold,
            "cost_ratio": self.models.info["cost_ratio"],
            "plc": {"connected": time.monotonic() - self.last_row_at < 2.0, "last_seq": self.last_seq,
                    "io_ms": r(self.plc_io_ms), "missed_cycles": self.missed, **demo},
            "edge": {"inference_ms": r(self.inference_ms)},
            "uplink": {"state": self.sender.uplink_state(),          # up | down | simulated_down
                       "outage_remaining_s": round(self.sender.outage_remaining(), 1),
                       "ack_ms": r(self.sender.last_ack_ms),          # publish -> PUBACK
                       "buffer_backlog": len(self.outbox),
                       "buffer_dropped": self.outbox.dropped,
                       "buffer_max": self.outbox.max_rows},
            "last_command": self.last_command,
        }

    async def publish_status(self):
        try:
            demo = await self.plc.read_demo()
        except Exception:  # noqa: BLE001 — PLC unreachable: still report the rest
            demo = {"drift_mode": None, "cycle_ms": None}
        # QoS 0: while the broker is really down this is dropped instead of piling up in paho's
        # memory (a stale heartbeat is useless). Retained: a new dashboard sees it at once.
        self.mqtt.publish(TOPIC_STATUS, json.dumps(self.status(demo)), qos=0, retain=True)

    async def status_loop(self):
        while True:
            await self.publish_status()
            await asyncio.sleep(STATUS_INTERVAL_S)

    async def command_loop(self):
        while True:
            payload = await self.commands.get()
            try:
                cmd = commands.parse(payload)
                if cmd["cmd"] == "drift":
                    await self.plc.write_demo(drift=cmd["on"])
                elif cmd["cmd"] == "speed":
                    await self.plc.write_demo(cycle_ms=cmd["cycle_ms"])
                elif cmd["cmd"] == "outage":
                    self.sender.simulate_outage(cmd["seconds"])
                self.last_command = {**cmd, "ok": True}
                log.info("command %s", cmd)
            except ValueError as ex:
                self.last_command = {"ok": False, "error": str(ex)}
                log.warning("rejected command %r: %s", payload[:200], ex)
            await self.publish_status()  # answer at once, not up to 1 s later


async def run():
    await Edge().run()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
