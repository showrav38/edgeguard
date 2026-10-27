"""Milestone 3 check: PLC -> edge app -> PLC write-back + MQTT, over 500 live readings.

Needs the MQTT broker (`make broker`; `make check-m3` starts it). Starts its own PLC simulator
(port 4841, 100 ms cycle, latency log on) and the edge app as subprocesses, then checks:
  1. retained status message (running, model version, threshold, cost ratio)
  2. 500 telemetry messages, all fields present, Seq without gaps -> 0 missed cycles
  3. live risk/alarm == offline ONNX on the same test rows (proves transport + feature mapping)
  4. every message has 2 top factors and an action code that fits its state
  5. write-back: the PLC holds the edge app's last results
  6. end-to-end latency (PLC writes Seq -> edge writes LastSeqProcessed), measured in the PLC
  7. inference time; live precision / recall vs ground truth (reported)
Prints PASS or FAIL and exits with code 0 / 1.
"""
import asyncio
import csv
import json
import os
import queue
import subprocess
import sys
import tempfile
import time

import numpy as np
import onnxruntime as ort
import paho.mqtt.client as mqtt
from asyncua import Client, ua

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "training"))
import common as c  # noqa: E402

N = 500
PORT = 4841
CYCLE_MS = 100
MAX_E2E_P95_MS = CYCLE_MS / 2        # must finish well within one PLC cycle
MAX_INFERENCE_P95_MS = 10.0
# Own topics and MQTT client id: a dev stack running on the same broker cannot mix in.
PREFIX = "edgeguard-check-m3/machine1"
ISOLATION_ENV = {"MQTT_CLIENT_ID": "edgeguard-edge-check-m3", "MQTT_TOPIC_TELEMETRY": f"{PREFIX}/telemetry",
                 "MQTT_TOPIC_STATUS": f"{PREFIX}/status", "MQTT_TOPIC_CMD": f"{PREFIX}/cmd"}
TOPIC_T, TOPIC_S = ISOLATION_ENV["MQTT_TOPIC_TELEMETRY"], ISOLATION_ENV["MQTT_TOPIC_STATUS"]
FIELDS = ["seq", "ts", "signals", "risk", "alarm", "failure_type", "action", "top_factors",
          "drift_score", "drift_warning", "inference_ms", "buffered", "ground_truth"]
STATE_CODES = {"OK": {0}, "WARNING": {5}, "ALARM": {1, 2, 3, 4}}

errors = []


def check(ok: bool, msg: str):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        errors.append(msg)


def start_subscriber(inbox: queue.Queue) -> mqtt.Client:
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-check-m3")
    sub.on_connect = lambda cl, *a: cl.subscribe([(TOPIC_T, 1), (TOPIC_S, 1)])
    sub.on_message = lambda cl, u, m: inbox.put((m.topic, json.loads(m.payload), m.retain))
    sub.connect("localhost", 1883)
    sub.loop_start()
    return sub


async def read_plc_outputs() -> dict:
    client = Client(f"opc.tcp://127.0.0.1:{PORT}/edgeguard/")
    await client.connect()
    try:
        ns = await client.get_namespace_index("urn:edgeguard:plc")
        names = ["LastSeqProcessed", "FailureRisk", "Alarm", "ActionCode", "ModelVersion", "InferenceMs",
                 "AlarmLatched", "AlarmAcknowledged"]
        values = await client.read_values([client.get_node(ua.NodeId(f"Machine1.{n}", ns)) for n in names])
        return dict(zip(names, values))
    finally:
        await client.disconnect()


def main() -> int:
    info = json.load(open(os.path.join(ROOT, "models", "model_info.json")))
    tmp = tempfile.mkdtemp(prefix="edgeguard-m3-")
    latency_log = os.path.join(tmp, "latency.csv")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    plc_env = {**env, "OPCUA_ENDPOINT": f"opc.tcp://0.0.0.0:{PORT}/edgeguard/", "PLC_CYCLE_MS": str(CYCLE_MS),
               "PLC_LATENCY_LOG": latency_log}
    # Own SQLite outbox too: two edge apps sharing one outbox file would send each other's rows.
    edge_env = {**env, **ISOLATION_ENV, "OPCUA_URL": f"opc.tcp://127.0.0.1:{PORT}/edgeguard/",
                "BUFFER_PATH": os.path.join(tmp, "outbox.db")}
    plc_log, edge_log = open(os.path.join(tmp, "plc.log"), "w"), open(os.path.join(tmp, "edge.log"), "w")

    inbox: queue.Queue = queue.Queue()
    try:
        sub = start_subscriber(inbox)
    except OSError as ex:
        check(False, f"MQTT broker not reachable on localhost:1883 ({ex}); run `make broker`")
        print("M3 check: FAIL")
        return 1

    plc = subprocess.Popen([sys.executable, "-m", "plc_sim.server"], cwd=ROOT, env=plc_env,
                           stdout=plc_log, stderr=subprocess.STDOUT)
    time.sleep(1.5)
    edge = subprocess.Popen([sys.executable, "-m", "edge_app.main"], cwd=ROOT, env=edge_env,
                            stdout=edge_log, stderr=subprocess.STDOUT)
    msgs, status = [], None
    try:
        print(f"collecting {N} readings at {CYCLE_MS} ms (~{N * CYCLE_MS / 1000 + 5:.0f} s) ...")
        deadline = time.time() + N * CYCLE_MS / 1000 * 2 + 30
        while len(msgs) < N and time.time() < deadline:
            try:
                topic, payload, retain = inbox.get(timeout=1)
            except queue.Empty:
                if edge.poll() is not None:
                    break  # edge app crashed
                continue
            if topic == TOPIC_S:
                status = payload
            else:
                msgs.append(payload)

        edge.terminate()
        edge.wait(timeout=5)
        time.sleep(0.3)
        while not inbox.empty():  # messages published just before the edge app stopped
            topic, payload, _ = inbox.get()
            if topic == TOPIC_T:
                msgs.append(payload)
        outputs = asyncio.run(read_plc_outputs())
    finally:
        for p in (edge, plc):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        sub.loop_stop()

    if len(msgs) < N:
        check(False, f"only {len(msgs)} telemetry messages received (logs in {tmp})")
        print(open(os.path.join(tmp, "edge.log")).read()[-2000:])
        print("M3 check: FAIL")
        return 1
    last_msg = msgs[-1]
    window = msgs[:N]

    # 1. Retained status.
    check(status is not None and status.get("state") == "running" and status.get("model_version") == info["model_version"]
          and status.get("threshold") == info["threshold"] and status.get("cost_ratio") == info["cost_ratio"],
          f"status: {status}")

    # 2. Completeness and missed cycles.
    missing = {f for m in window for f in FIELDS if f not in m}
    check(not missing, f"all telemetry fields present {sorted(missing) or ''}")
    seqs = [m["seq"] for m in window]
    gaps = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]))
    check(gaps == 0 and len(set(seqs)) == N,
          f"{N} readings, Seq {seqs[0]}..{seqs[-1]}, missed cycles: {gaps} (edge counter: {window[-1]['missed_cycles']})")

    # 3. Live == offline ONNX on the same rows.
    rows = list(csv.DictReader(open(os.path.join(c.DATA_DIR, "test.csv"))))
    import pandas as pd
    test = pd.read_csv(os.path.join(c.DATA_DIR, "test.csv"))
    idx = [(s - 1) % len(rows) for s in seqs]
    sess = ort.InferenceSession(os.path.join(ROOT, "models", "risk.onnx"), providers=["CPUExecutionProvider"])
    offline = sess.run(["probabilities"], {sess.get_inputs()[0].name: c.X(test.iloc[idx])})[0][:, 1]
    live = np.array([m["risk"] for m in window])
    live_alarm = np.array([m["alarm"] for m in window])
    max_diff = float(np.abs(live - offline).max())
    same_alarm = int((live_alarm == (offline >= info["threshold"])).sum())
    # live risk is rounded to 4 decimals in the message
    check(max_diff <= 1e-4 and same_alarm == N,
          f"live == offline ONNX: max risk diff {max_diff:.1e}, same alarm decision {same_alarm}/{N}")

    # 4. Explanation and action.
    bad_factors = sum(len(m["top_factors"]) != 2 for m in window)
    bad_codes = sum(m["action"]["code"] not in STATE_CODES[m["state"]] for m in window)
    states = {s: sum(m["state"] == s for m in window) for s in STATE_CODES}
    check(bad_factors == 0 and bad_codes == 0,
          f"2 top factors and matching action code in every message (states: {states})")

    # 5. Write-back.
    if outputs["LastSeqProcessed"] == last_msg["seq"]:
        ok = (abs(outputs["FailureRisk"] - last_msg["risk"]) < 1e-4 and outputs["Alarm"] == last_msg["alarm"]
              and outputs["ActionCode"] == last_msg["action"]["code"] and outputs["ModelVersion"] == info["model_version"])
    else:  # stopped between write-back and publish: PLC is one row ahead of MQTT
        ok = outputs["LastSeqProcessed"] == last_msg["seq"] + 1
    # Alarm state machine (D-32): nobody acknowledges in this check, so any ALARM leaves an alarm latched.
    latch_ok = (outputs["AlarmLatched"] or not live_alarm.any()) and outputs["AlarmAcknowledged"] is False
    check(ok and latch_ok, f"PLC write-back: LastSeqProcessed={outputs['LastSeqProcessed']} (last MQTT seq "
              f"{last_msg['seq']}), FailureRisk={outputs['FailureRisk']:.4f}, ModelVersion={outputs['ModelVersion']}, "
              f"AlarmLatched={outputs['AlarmLatched']}, AlarmAcknowledged={outputs['AlarmAcknowledged']}")

    # 6. End-to-end latency, measured in the PLC process.
    lat = {}
    with open(latency_log) as fh:
        for r in csv.DictReader(fh):
            lat[int(r["seq"])] = float(r["latency_ms"])
    e2e = np.array([lat[s] for s in seqs if s in lat])
    if len(e2e):
        mean, p95 = float(e2e.mean()), float(np.percentile(e2e, 95))
        check(len(e2e) == N and p95 <= MAX_E2E_P95_MS,
              f"end-to-end latency (Seq write -> LastSeqProcessed write), {len(e2e)} rows: "
              f"mean {mean:.1f} ms, p95 {p95:.1f} ms, max {e2e.max():.1f} ms (p95 limit {MAX_E2E_P95_MS:.0f} ms)")
    else:
        check(False, "no end-to-end latency measurements in the PLC log")

    # 7. Inference time + live quality.
    inf = np.array([m["inference_ms"] for m in window])
    check(float(np.percentile(inf, 95)) <= MAX_INFERENCE_P95_MS,
          f"inference + explanation + action: mean {inf.mean():.2f} ms, p95 {np.percentile(inf, 95):.2f} ms")
    y = np.array([m["ground_truth"]["failure"] for m in window])
    tp = int((live_alarm & y).sum())
    precision = tp / max(int(live_alarm.sum()), 1)
    recall = tp / max(int(y.sum()), 1)
    print(f"  info live quality on {N} readings: {int(y.sum())} failures, {int(live_alarm.sum())} alarms, "
          f"TP={tp} -> precision {precision:.2f}, recall {recall:.2f} (few failures -> noisy)")

    alarm_msgs = [m for m in window if m["state"] == "ALARM" and m["ground_truth"]["failure"]]
    if alarm_msgs:
        m = alarm_msgs[0]
        print(f"  info example alarm: seq {m['seq']} risk {m['risk']:.2f} -> {m['action']['text']} "
              f"factors {m['top_factors']} (truth: {m['ground_truth']['type']})")

    print(f"  logs: {tmp}")
    print("M3 check:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
