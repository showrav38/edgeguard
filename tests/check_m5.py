"""Milestone 5 check: dashboard build + the edge app features the dashboard relies on.

Part 1 (dashboard): unit tests (duplicate filter by seq, history, alarm events), TypeScript check and
production build. Uses Linux Node from nvm if `node` is under ~/.nvm, otherwise a node:22 container.

Part 2 (live, needs the broker: `make check-m5` starts it). Starts its own PLC simulator (port 4843,
100 ms cycle) and edge app with a SMALL buffer (BUFFER_MAX_ROWS=50), then acts like the dashboard:
  A. status heartbeat every ~1 s with PLC / edge / uplink health
  B. command "speed" 50 ms -> PLC cycle changes, telemetry rate goes up; back to 100 ms
  C. command "drift" on -> status drift_mode + drift warning in telemetry; off again
  D. invalid command -> rejected and reported, edge app keeps running
  E. command "outage" 10 s -> uplink "simulated_down" while OUR connection stays up (heartbeat keeps
     coming), backlog grows, no live telemetry; afterwards buffered messages arrive in order.
     ~100 messages into a 50-row buffer: every missing seq must be accounted for by buffer_dropped.
Prints PASS or FAIL and exits with code 0 / 1.
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard")
PORT = 4843
CYCLE_MS = 100
OUTAGE_S = 10
BUFFER_MAX_ROWS = 50
TOPIC_T, TOPIC_S, TOPIC_C = ("edgeguard/machine1/telemetry", "edgeguard/machine1/status",
                             "edgeguard/machine1/cmd")
NODE_IMAGE = "node:22.11.0-bookworm-slim"

errors = []


def check(ok: bool, msg: str):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        errors.append(msg)


# --------------------------------------------------------------------------------------------
# Part 1: dashboard
# --------------------------------------------------------------------------------------------
def node_cmd(script: str) -> list[str]:
    node = shutil.which("node") or ""
    if node.startswith(os.path.expanduser("~/.nvm")):
        return ["bash", "-c", script]
    # No nvm Node: same build inside a container, files owned by the current user.
    return ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
            "-v", f"{DASH}:/app", "-w", "/app", NODE_IMAGE, "sh", "-c", script]


def check_dashboard():
    node = shutil.which("node") or "none"
    where = "nvm Node " + node if node.startswith(os.path.expanduser("~/.nvm")) else f"Docker {NODE_IMAGE}"
    print(f"1. dashboard: tests + type check + build ({where}) ...")
    install = "npm ci --no-audit --no-fund --loglevel=error" if os.path.exists(
        os.path.join(DASH, "package-lock.json")) else "npm install --no-audit --no-fund --loglevel=error"
    for step, script in [("npm install", install), ("unit tests (vitest)", "npm test"),
                         ("type check + build", "npm run build")]:
        r = subprocess.run(node_cmd(script), cwd=DASH, capture_output=True, text=True)
        check(r.returncode == 0, step)
        if r.returncode != 0:
            print((r.stdout + r.stderr)[-2000:])
            return


# --------------------------------------------------------------------------------------------
# Part 2: live
# --------------------------------------------------------------------------------------------
class Inbox:
    """Collects telemetry and FRESH status messages (the broker may hold an old retained one)."""

    def __init__(self, since: datetime):
        self.q: queue.Queue = queue.Queue()
        self.since = since
        self.telemetry, self.status = [], []

    def on_message(self, client, userdata, m):
        self.q.put((m.topic, time.time(), json.loads(m.payload)))

    def until(self, done, timeout_s: float) -> bool:
        """Consume messages until done(topic, msg) is true or the timeout passes."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                topic, t, msg = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if topic == TOPIC_S:
                if "ts" not in msg or datetime.fromisoformat(msg["ts"]) < self.since:
                    continue  # retained status from an earlier run, or the short on-connect one
                self.status.append((t, msg))
            else:
                self.telemetry.append((t, msg))
            if done(topic, msg):
                return True
        return False

    def wait(self, seconds: float):
        self.until(lambda *_: False, seconds)


def check_live():
    tmp = tempfile.mkdtemp(prefix="edgeguard-m5-")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    plc_env = {**env, "OPCUA_ENDPOINT": f"opc.tcp://0.0.0.0:{PORT}/edgeguard/", "PLC_CYCLE_MS": str(CYCLE_MS)}
    edge_env = {**env, "OPCUA_URL": f"opc.tcp://127.0.0.1:{PORT}/edgeguard/",
                "BUFFER_PATH": os.path.join(tmp, "outbox.db"), "BUFFER_MAX_ROWS": str(BUFFER_MAX_ROWS)}
    logs = {n: open(os.path.join(tmp, f"{n}.log"), "w") for n in ("plc", "edge")}

    inbox = Inbox(datetime.now(timezone.utc))
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edgeguard-check-m5-{os.getpid()}")
    sub.on_connect = lambda c, *a: c.subscribe([(TOPIC_T, 1), (TOPIC_S, 1)])
    sub.on_message = inbox.on_message
    try:
        sub.connect("localhost", 1883)
    except OSError as ex:
        check(False, f"MQTT broker not reachable on localhost:1883 ({ex}); run `make broker`")
        return
    sub.loop_start()
    send = lambda cmd: sub.publish(TOPIC_C, json.dumps(cmd) if isinstance(cmd, dict) else cmd, qos=1)  # noqa: E731
    last_status = lambda: inbox.status[-1][1]  # noqa: E731

    plc = subprocess.Popen([sys.executable, "-m", "plc_sim.server"], cwd=ROOT, env=plc_env,
                           stdout=logs["plc"], stderr=subprocess.STDOUT)
    time.sleep(1.5)
    edge = subprocess.Popen([sys.executable, "-m", "edge_app.main"], cwd=ROOT, env=edge_env,
                            stdout=logs["edge"], stderr=subprocess.STDOUT)
    try:
        # --- A. heartbeat ---------------------------------------------------------------------
        print("2A. status heartbeat ...")
        ok = inbox.until(lambda tp, m: tp == TOPIC_S and m["plc"]["connected"], 30)
        if not ok:
            raise RuntimeError("no fresh status with a connected PLC from the edge app")
        n0 = len(inbox.status)
        inbox.wait(5.0)
        rate = len(inbox.status) - n0
        s = last_status()
        check(4 <= rate <= 7, f"{rate} status messages in 5 s (every ~1 s)")
        check(s["uplink"]["state"] == "up" and s["plc"]["cycle_ms"] == CYCLE_MS
              and s["edge"]["inference_ms"] is not None and s["uplink"]["ack_ms"] is not None,
              f"status: uplink {s['uplink']['state']}, PUBACK {s['uplink']['ack_ms']} ms, "
              f"inference {s['edge']['inference_ms']} ms, PLC io {s['plc']['io_ms']} ms, cycle {s['plc']['cycle_ms']} ms")

        # --- B. replay speed ------------------------------------------------------------------
        print("2B. command speed 50 ms ...")
        send({"cmd": "speed", "cycle_ms": 50})
        inbox.until(lambda tp, m: tp == TOPIC_S and m["plc"]["cycle_ms"] == 50, 5)
        n0 = len(inbox.telemetry)
        inbox.wait(3.0)
        fast = len(inbox.telemetry) - n0
        check(last_status()["plc"]["cycle_ms"] == 50 and fast >= 45,
              f"PLC cycle 50 ms: {fast} telemetry messages in 3 s (100 ms cycle would give ~30)")
        send({"cmd": "speed", "cycle_ms": CYCLE_MS})
        inbox.until(lambda tp, m: tp == TOPIC_S and m["plc"]["cycle_ms"] == CYCLE_MS, 5)

        # --- C. drift -------------------------------------------------------------------------
        print("2C. command drift on / off ...")
        send({"cmd": "drift", "on": True})
        found = inbox.until(lambda tp, m: tp == TOPIC_T and m["drift_warning"], 30)
        check(found and last_status()["plc"]["drift_mode"] is True,
              f"drift on: status drift_mode {last_status()['plc']['drift_mode']}, drift warning {found}")
        send({"cmd": "drift", "on": False})
        cleared = inbox.until(lambda tp, m: tp == TOPIC_T and not m["drift_warning"], 30)
        check(cleared, "drift off: warning cleared")

        # --- D. invalid command ---------------------------------------------------------------
        print("2D. invalid commands ...")
        send(b"not json")
        send({"cmd": "speed", "cycle_ms": 1})
        got = inbox.until(lambda tp, m: tp == TOPIC_S and (m.get("last_command") or {}).get("ok") is False
                          and "cycle_ms" in m["last_command"]["error"], 5)
        check(got and edge.poll() is None,
              f"rejected and reported: {last_status().get('last_command')}; edge app still running")

        # --- E. simulated outage --------------------------------------------------------------
        print(f"2E. command outage {OUTAGE_S} s (buffer limit {BUFFER_MAX_ROWS}) ...")
        inbox.wait(1.0)
        dropped_before = last_status()["uplink"]["buffer_dropped"]
        before = inbox.telemetry[-1][1]["seq"]
        t_cmd = time.time()
        send({"cmd": "outage", "seconds": OUTAGE_S})
        inbox.until(lambda tp, m: tp == TOPIC_S and m["uplink"]["state"] == "simulated_down", 5)
        inbox.until(lambda tp, m: tp == TOPIC_S and m["uplink"]["state"] == "up", OUTAGE_S + 5)
        during = [m for t, m in inbox.status if t_cmd < t and m["uplink"]["state"] == "simulated_down"]
        max_backlog = max((m["uplink"]["buffer_backlog"] for m in during), default=0)
        live_during = [m for t, m in inbox.telemetry if t_cmd + 1.0 < t < t_cmd + OUTAGE_S - 0.5]
        check(len(during) >= OUTAGE_S - 2,
              f"our connection stayed up: {len(during)} heartbeats with uplink 'simulated_down'")
        check(max_backlog == BUFFER_MAX_ROWS and not live_during,
              f"backlog grew to {max_backlog} (limit {BUFFER_MAX_ROWS}); live telemetry during outage: {len(live_during)}")

        drained = inbox.until(lambda tp, m: tp == TOPIC_T and not m["buffered"] and m["seq"] > before + 120, 30)
        inbox.wait(2.0)  # one more heartbeat
        dropped = last_status()["uplink"]["buffer_dropped"] - dropped_before
        check(drained and last_status()["uplink"]["buffer_backlog"] == 0, "buffer drained, live telemetry again")

        # Dedup by seq (as the dashboard does), then: in order, and every gap explained by drops.
        seqs, seen = [], set()
        for _, m in inbox.telemetry:
            if m["seq"] > before and m["seq"] not in seen:
                seen.add(m["seq"])
                seqs.append(m["seq"])
        missing = (max(seqs) - before) - len(seqs)
        in_order = all(a < b for a, b in zip(seqs, seqs[1:]))
        buffered = sum(1 for _, m in inbox.telemetry if m["seq"] > before and m["buffered"])
        check(in_order and dropped > 0 and missing == dropped,
              f"after outage: {buffered} buffered msgs delivered, in order {in_order}; "
              f"{missing} missing seqs = {dropped} reported as dropped (buffer full)")
    except Exception as ex:  # noqa: BLE001
        check(False, f"{type(ex).__name__}: {ex}")
        print(open(os.path.join(tmp, "edge.log")).read()[-2000:])
    finally:
        for p in (edge, plc):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        sub.loop_stop()


def main() -> int:
    check_dashboard()
    check_live()
    print("M5 check:", "FAIL" if errors else "PASS")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
