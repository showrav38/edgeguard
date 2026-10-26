"""EdgeGuard edge app: PLC (OPC UA) -> ONNX -> explanation -> action -> PLC write-back + MQTT.

One loop iteration per PLC cycle:
  1. wait for a new Seq, read the full row
  2. risk + failure type (ONNX), top-2 factors (occlusion), state + action text
  3. write results back to the PLC (one write request, LastSeqProcessed last)
  4. publish telemetry over MQTT, through the store-and-forward buffer (M4)
Write-back comes before MQTT: the PLC is the time-critical consumer, and it keeps getting
results while the MQTT/IT side is down. A drift monitor (M4) flags readings unlike the training data.

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

from edge_app import actions, explain
from edge_app.buffer import Outbox, StoreAndForward
from edge_app.drift import DriftMonitor
from edge_app.inference import Models
from edge_app.opcua_client import PlcClient

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_TELEMETRY = os.getenv("MQTT_TOPIC_TELEMETRY", "edgeguard/machine1/telemetry")
TOPIC_STATUS = os.getenv("MQTT_TOPIC_STATUS", "edgeguard/machine1/status")

log = logging.getLogger("edge")


def make_mqtt(models: Models) -> mqtt.Client:
    """paho-mqtt client running its network loop in a background thread."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-edge")
    status = {"model_version": models.version, "threshold": models.threshold,
              "cost_ratio": models.info["cost_ratio"]}
    # If we die without saying goodbye, the broker publishes this retained "offline" status.
    client.will_set(TOPIC_STATUS, json.dumps({"state": "offline", **status}), qos=1, retain=True)

    def on_connect(c, userdata, flags, reason_code, properties):
        log.info("MQTT connected to %s:%d (%s)", MQTT_HOST, MQTT_PORT, reason_code)
        c.publish(TOPIC_STATUS, json.dumps({"state": "running", **status}), qos=1, retain=True)

    client.on_connect = on_connect
    # Retry every 1-5 s while the broker is down (paho's default backs off up to 2 minutes).
    client.reconnect_delay_set(min_delay=1, max_delay=5)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=10)
    client.loop_start()  # reconnects automatically
    return client


def telemetry(seq, signals, pred, decision, factors, drift, inference_ms, ground_truth, missed) -> dict:
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
        # "buffered" (stored while MQTT was down) and "buffer_backlog" are set by StoreAndForward
        "ground_truth": ground_truth,
        "missed_cycles": missed,
    }


async def run():
    models = Models()
    log.info("models loaded: version %s, threshold %.2f", models.version, models.threshold)
    mqttc = make_mqtt(models)
    sender = StoreAndForward(mqttc, Outbox())
    drift_monitor = DriftMonitor(models.info["training_stats"])
    plc = PlcClient()
    await plc.connect()
    queue = await plc.subscribe_seq()

    last_seq, missed, n = None, 0, 0
    inf_ms: list[float] = []
    while True:
        await queue.get()
        seq, signals, ground_truth, drift_mode = await plc.read_row()
        if seq == 0 or (last_seq is not None and seq <= last_seq):
            continue  # PLC not started yet, or we already handled this row (read was ahead)
        if last_seq is not None:
            missed += seq - last_seq - 1

        t0 = time.perf_counter()
        pred = models.predict(signals)
        factors = explain.top_factors(models, pred["x"], pred["risk"])
        decision = actions.decide(pred, signals, models.threshold)
        drift = drift_monitor.update(signals)
        inference_ms = (time.perf_counter() - t0) * 1000

        await plc.write_outputs({
            "FailureRisk": pred["risk"],
            "Alarm": pred["alarm"],
            "FailureType": decision["failure_type"],
            "ActionCode": decision["code"],
            "DriftWarning": drift["warning"],
            "InferenceMs": inference_ms,
            "ModelVersion": models.version,
            "LastSeqProcessed": seq,
        })
        msg = telemetry(seq, signals, pred, decision, factors, drift, inference_ms, ground_truth, missed)
        sender.send(TOPIC_TELEMETRY, msg)  # stored in SQLite first, sent in order by a thread

        last_seq, n = seq, n + 1
        inf_ms.append(inference_ms)
        if n % 100 == 0:
            log.info("seq=%d processed=%d missed=%d inference mean %.2f ms (last 100) "
                     "drift=%s backlog=%d", seq, n, missed, statistics.mean(inf_ms),
                     drift["score"], msg["buffer_backlog"])
            inf_ms.clear()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
