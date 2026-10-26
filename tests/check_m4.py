"""Milestone 4 check: robustness — drift warning and store-and-forward through a broker outage.

Needs Docker and the broker container (`make check-m4` starts it). Starts its own PLC simulator
(port 4842, 100 ms cycle) and the edge app (fresh SQLite outbox), then:
  A. normal replay: no drift warning
  B. DriftMode on  -> DriftWarning within one window (MQTT and the PLC node), names a shifted signal
  C. DriftMode off -> the warning clears
  D. `docker stop` the broker for 20 s, then `docker start`:
       - the PLC keeps getting results during the outage (LastSeqProcessed advances)
       - 0 lost messages, in order (first arrival), messages from the outage marked buffered
       - 0 missed PLC cycles
The check's subscriber uses a persistent session (clean_session=False) and the broker has
persistence on, so the subscriber cannot miss messages while it reconnects itself.
Prints PASS or FAIL and exits with code 0 / 1.
"""
import asyncio
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import time

import paho.mqtt.client as mqtt
from asyncua import Client, ua

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from edge_app.drift import DRIFT_ON, DRIFT_WINDOW  # noqa: E402

PORT = 4842
CYCLE_MS = 100
OUTAGE_S = 20
BROKER = "edgeguard-mosquitto"
TOPIC_T = "edgeguard/machine1/telemetry"
PLC_URL = f"opc.tcp://127.0.0.1:{PORT}/edgeguard/"

errors = []
logging.getLogger("asyncua").setLevel(logging.ERROR)  # hide session-timeout notices


def check(ok: bool, msg: str):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        errors.append(msg)


def start_subscriber(inbox: queue.Queue) -> mqtt.Client:
    # Persistent session: the broker queues our QoS 1 messages while we are disconnected.
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edgeguard-check-m4-{os.getpid()}",
                      clean_session=False)
    sub.on_connect = lambda cl, *a: cl.subscribe(TOPIC_T, qos=1)
    sub.on_message = lambda cl, u, m: inbox.put((time.time(), json.loads(m.payload)))
    sub.reconnect_delay_set(min_delay=1, max_delay=2)
    sub.connect("localhost", 1883)
    sub.loop_start()
    return sub


async def plc_io(write: dict | None = None, read: list[str] = ()) -> dict:
    """Write / read Machine1 variables on the simulated PLC."""
    client = Client(PLC_URL)
    await client.connect()
    try:
        ns = await client.get_namespace_index("urn:edgeguard:plc")
        node = lambda n: client.get_node(ua.NodeId(f"Machine1.{n}", ns))  # noqa: E731
        for name, value in (write or {}).items():
            await node(name).write_value(ua.DataValue(ua.Variant(value, ua.VariantType.Boolean)))
        return dict(zip(read, await client.read_values([node(n) for n in read]))) if read else {}
    finally:
        await client.disconnect()


class Collector:
    """Pulls telemetry from the subscriber queue, keeps all of it, stops on a condition."""

    def __init__(self, inbox: queue.Queue, edge: subprocess.Popen):
        self.inbox, self.edge, self.msgs, self.arrival = inbox, edge, [], []

    def until(self, done, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                t, m = self.inbox.get(timeout=0.5)
            except queue.Empty:
                if self.edge.poll() is not None:
                    return False  # edge app crashed
                continue
            self.msgs.append(m)
            self.arrival.append(t)
            if done(m):
                return True
        return False


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="edgeguard-m4-")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    plc_env = {**env, "OPCUA_ENDPOINT": f"opc.tcp://0.0.0.0:{PORT}/edgeguard/", "PLC_CYCLE_MS": str(CYCLE_MS)}
    edge_env = {**env, "OPCUA_URL": PLC_URL, "BUFFER_PATH": os.path.join(tmp, "outbox.db")}
    plc_log, edge_log = open(os.path.join(tmp, "plc.log"), "w"), open(os.path.join(tmp, "edge.log"), "w")

    inbox: queue.Queue = queue.Queue()
    try:
        sub = start_subscriber(inbox)
    except OSError as ex:
        check(False, f"MQTT broker not reachable on localhost:1883 ({ex}); run `make broker`")
        print("M4 check: FAIL")
        return 1

    plc = subprocess.Popen([sys.executable, "-m", "plc_sim.server"], cwd=ROOT, env=plc_env,
                           stdout=plc_log, stderr=subprocess.STDOUT)
    time.sleep(1.5)
    edge = subprocess.Popen([sys.executable, "-m", "edge_app.main"], cwd=ROOT, env=edge_env,
                            stdout=edge_log, stderr=subprocess.STDOUT)
    col = Collector(inbox, edge)
    t_restart = time.time()
    try:
        # --- A. normal data -------------------------------------------------------------------
        print(f"A. normal replay ({CYCLE_MS} ms cycle, drift window {DRIFT_WINDOW}) ...")
        if not col.until(lambda m: len(col.msgs) >= DRIFT_WINDOW + 50, 60):
            raise RuntimeError("no telemetry from the edge app")
        scores = [m["drift_score"] for m in col.msgs if m["drift_score"] is not None]
        check(not any(m["drift_warning"] for m in col.msgs) and max(scores) < DRIFT_ON,
              f"no drift warning on {len(col.msgs)} normal readings (max score {max(scores):.2f} < {DRIFT_ON})")

        # --- B. drift mode on -----------------------------------------------------------------
        print("B. DriftMode on (+8 K, +300 rpm) ...")
        asyncio.run(plc_io(write={"DriftMode": True}))
        on_seq = col.msgs[-1]["seq"]
        found = col.until(lambda m: m["drift_warning"], 30)
        delay = col.msgs[-1]["seq"] - on_seq
        check(found and delay <= DRIFT_WINDOW,
              f"drift warning after {delay} readings (limit {DRIFT_WINDOW}), score {col.msgs[-1]['drift_score']}, "
              f"signals {col.msgs[-1]['drift_signals']}")
        # ProcessTempK usually crosses first: same +8 K, but a smaller training std (1.5 vs 2.0 K).
        named = set(col.msgs[-1]["drift_signals"])
        check(named and named <= {"AirTempK", "ProcessTempK", "SpeedRpm"},
              f"drifted signals {sorted(named)} are among the shifted ones (TorqueNm unchanged)")
        col.until(lambda m: False, 1.0)  # let the PLC catch the next write-back
        plc_state = asyncio.run(plc_io(read=["DriftWarning"]))
        check(plc_state["DriftWarning"] is True, f"PLC node DriftWarning = {plc_state['DriftWarning']}")

        # --- C. drift mode off ----------------------------------------------------------------
        print("C. DriftMode off ...")
        asyncio.run(plc_io(write={"DriftMode": False}))
        off_seq = col.msgs[-1]["seq"]
        cleared = col.until(lambda m: not m["drift_warning"], 30)
        check(cleared, f"drift warning cleared after {col.msgs[-1]['seq'] - off_seq} readings")

        # --- D. broker outage -----------------------------------------------------------------
        print(f"D. stopping the MQTT broker for {OUTAGE_S} s ...")
        col.until(lambda m: False, 2.0)
        before = asyncio.run(plc_io(read=["LastSeqProcessed"]))["LastSeqProcessed"]
        subprocess.run(["docker", "stop", BROKER], check=True, capture_output=True)
        time.sleep(OUTAGE_S)
        during = asyncio.run(plc_io(read=["LastSeqProcessed"]))["LastSeqProcessed"]
        subprocess.run(["docker", "start", BROKER], check=True, capture_output=True)
        t_restart = time.time()
        check(during - before >= OUTAGE_S * 1000 / CYCLE_MS * 0.8,
              f"PLC kept getting results during the outage: LastSeqProcessed {before} -> {during}")

        # Wait until the backlog has drained and live (non-buffered) messages flow again.
        print("   broker started again, waiting for the buffered messages ...")
        drained = col.until(lambda m: m["seq"] > during + 30 and not m["buffered"], 60)
        edge.terminate()
        edge.wait(timeout=5)
        col.until(lambda m: False, 1.0)
        check(drained, "live telemetry flows again after the outage")
    except Exception as ex:  # noqa: BLE001 — report, then show the logs
        check(False, f"{type(ex).__name__}: {ex}")
    finally:
        subprocess.run(["docker", "start", BROKER], capture_output=True)  # never leave it stopped
        for p in (edge, plc):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        sub.loop_stop()

    msgs = col.msgs
    if not msgs:
        print(open(os.path.join(tmp, "edge.log")).read()[-2000:])
        print("M4 check: FAIL")
        return 1

    # --- 0 lost, in order (by first arrival), duplicates reported -------------------------------
    first_seen, seqs_in_order = set(), []
    for m in msgs:
        if m["seq"] not in first_seen:
            first_seen.add(m["seq"])
            seqs_in_order.append(m["seq"])
    lo, hi = min(first_seen), max(first_seen)
    lost = (hi - lo + 1) - len(first_seen)
    in_order = all(a < b for a, b in zip(seqs_in_order, seqs_in_order[1:]))
    dups = len(msgs) - len(first_seen)
    check(lost == 0 and in_order,
          f"{len(first_seen)} messages, Seq {lo}..{hi}: lost {lost}, in order: {in_order} "
          f"(duplicates {dups}; QoS 1 = at least once)")

    buffered = [m for m in msgs if m["buffered"]]
    expect = OUTAGE_S * 1000 / CYCLE_MS
    check(len(buffered) >= expect * 0.8,
          f"{len(buffered)} messages marked buffered (outage ~{expect:.0f} readings), "
          f"max backlog {max(m['buffer_backlog'] for m in msgs)}")
    if buffered:
        last_buf = max(i for i, m in enumerate(msgs) if m["buffered"])
        print(f"  info backlog delivered {col.arrival[last_buf] - t_restart:.1f} s after the broker restart "
              f"(includes the edge app's reconnect delay, 1-5 s)")
    check(msgs[-1]["missed_cycles"] == 0, f"missed PLC cycles: {msgs[-1]['missed_cycles']}")

    print(f"  logs: {tmp}")
    print("M4 check:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
