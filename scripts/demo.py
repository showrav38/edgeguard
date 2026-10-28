"""2-minute scripted demo for a screen recording (M6). Needs the stack running (`make demo` starts it).

Drives the same demo commands as the dashboard's demo controls (edge_app/commands.py) over MQTT and
prints a short caption per step plus live lines from the telemetry, so the console tells the story
next to the dashboard (http://localhost:8080):
  1. normal operation          1 reading/s, risk low
  2. tool wear -> alarm        replay 10x faster until a tool-wear ALARM, then slow down to read it
  3. drift                     DriftMode on (+8 K, +300 rpm) until the drift warning appears
  4. uplink outage             simulated 20 s outage in the edge app; count buffered, lost, order

Run: .venv/bin/python scripts/demo.py      Config: MQTT_HOST, MQTT_PORT.
"""
import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_TELEMETRY = "edgeguard/machine1/telemetry"
TOPIC_STATUS = "edgeguard/machine1/status"
TOPIC_CMD = "edgeguard/machine1/cmd"

NORMAL_MS, FAST_MS, BRISK_MS = 1000, 100, 200  # same speeds as the dashboard's demo controls
OUTAGE_S = 20
PAUSE_S = 4  # after each caption: time to read it on the recording before things change


class Feed:
    """Collects telemetry (first copy of each seq only: delivery is at-least-once) and the latest status."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seqs: list[int] = []        # seqs in arrival order, duplicates removed
        self.seen: set[int] = set()
        self.buffered = 0                # messages marked buffered=true (stored during an outage)
        self.latest: dict | None = None
        self.status: dict | None = None

    def on_message(self, client, userdata, m):
        msg = json.loads(m.payload)
        with self.lock:
            if m.topic == TOPIC_STATUS:
                self.status = msg
                return
            if msg["seq"] in self.seen:
                return
            self.seen.add(msg["seq"])
            self.seqs.append(msg["seq"])
            self.buffered += bool(msg.get("buffered"))
            self.latest = msg


def caption(n: int, title: str, text: str):
    print(f"\n{'=' * 72}\n STEP {n}/4  {title}\n {text}\n{'=' * 72}", flush=True)
    time.sleep(PAUSE_S)


def live(m: dict) -> str:
    s = m["signals"]
    return (f"  seq {m['seq']:5d} | {m['state']:7s} | risk {m['risk'] * 100:5.1f} % | "
            f"tool wear {s['ToolWearMin']:3.0f} min | air {s['AirTempK']:.1f} K | "
            f"drift {'WARNING' if m['drift_warning'] else 'ok'}")


def wait_until(feed: Feed, done, timeout_s: float, every_s: float = 2.0) -> bool:
    """Print a live line every few seconds until done(latest) is true or the timeout passes."""
    t_end, t_print = time.time() + timeout_s, 0.0
    while time.time() < t_end:
        with feed.lock:
            m = feed.latest
        if m and done(m):
            return True
        if m and time.time() - t_print >= every_s:
            print(live(m), flush=True)
            t_print = time.time()
        time.sleep(0.1)
    return False


def main() -> int:
    feed = Feed()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-demo")
    client.on_connect = lambda c, *_: c.subscribe([(TOPIC_TELEMETRY, 1), (TOPIC_STATUS, 0)])
    client.on_message = feed.on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
    client.loop_start()
    send = lambda cmd: client.publish(TOPIC_CMD, json.dumps(cmd), qos=1)  # noqa: E731
    ok = True

    print("EdgeGuard demo. Dashboard: http://localhost:8080  (simulated PLC, public AI4I dataset)")
    if not wait_until(feed, lambda m: True, 10):
        print("FAIL: no telemetry from the edge app. Is the stack up? (make up)")
        return 1

    # --- 1. Normal operation ----------------------------------------------------------------
    caption(1, "Normal operation",
            "The simulated PLC sends one reading per second over OPC UA. The edge app scores each one.")
    send({"cmd": "drift", "on": False})
    send({"cmd": "speed", "cycle_ms": NORMAL_MS})
    wait_until(feed, lambda m: False, 20, every_s=3.0)

    # --- 2. Tool wear -> alarm -----------------------------------------------------------------
    caption(2, "Tool wearing out -> alarm and action",
            "Replay 10x faster. As the tool ages the risk climbs until it crosses the cost-based threshold.")
    send({"cmd": "speed", "cycle_ms": FAST_MS})
    if wait_until(feed, lambda m: m["state"] == "ALARM" and m["failure_type"] == "tool_wear", 45, every_s=1.0):
        send({"cmd": "speed", "cycle_ms": NORMAL_MS})  # slow down so the action card can be read
        m = feed.latest
        factors = ", ".join(f"{f['signal']} (+{f['effect'] * 100:.0f} pts)" for f in m["top_factors"])
        print(live(m))
        print(f"  >> ALARM, risk {m['risk'] * 100:.0f} %: {m['action']['text']}")
        print(f"  >> why: {factors}")
        time.sleep(15)
    else:
        print("  (no tool-wear alarm within 45 s)")
        ok = False

    # --- 3. Drift ------------------------------------------------------------------------------
    caption(3, "Unusual conditions (drift)",
            "Shop floor +8 K hotter, spindle +300 rpm. The model never saw this: EdgeGuard says so.")
    send({"cmd": "drift", "on": True})
    send({"cmd": "speed", "cycle_ms": BRISK_MS})
    t0, seq0 = time.time(), feed.latest["seq"]
    if wait_until(feed, lambda m: m["drift_warning"], 30):
        m = feed.latest
        print(live(m))
        print(f"  >> DRIFT WARNING after {m['seq'] - seq0} readings ({time.time() - t0:.0f} s), "
              f"signals: {', '.join(m['drift_signals'])}. Prediction may be unreliable.")
        time.sleep(10)
    else:
        print("  (no drift warning within 30 s)")
        ok = False
    send({"cmd": "drift", "on": False})

    # --- 4. Uplink outage, store-and-forward -------------------------------------------------
    caption(4, f"Network outage ({OUTAGE_S} s), nothing lost",
            "The uplink to plant IT is cut (simulated in the edge app). Readings are stored on the device.")
    with feed.lock:
        n_before, buffered_before = len(feed.seqs), feed.buffered
    send({"cmd": "outage", "seconds": OUTAGE_S})
    t_end = time.time() + OUTAGE_S + 30
    saw_down = False
    while time.time() < t_end:
        up = (feed.status or {}).get("uplink", {})
        if up.get("state") == "simulated_down":
            saw_down = True
            print(f"  uplink DOWN, {up['outage_remaining_s']:4.0f} s left | "
                  f"{up['buffer_backlog']:4d} readings stored on the edge device", flush=True)
        elif saw_down and up.get("buffer_backlog", 1) == 0:
            break
        time.sleep(2)
    time.sleep(1)  # let the last re-sent messages arrive
    with feed.lock:
        seqs = feed.seqs[n_before:]
        delivered = feed.buffered - buffered_before
    lost = (seqs[-1] - seqs[0] + 1 - len(seqs)) if seqs else 0
    in_order = seqs == sorted(seqs)
    print(f"  >> uplink back: {delivered} stored readings delivered, {lost} lost, "
          f"{'in order' if in_order else 'OUT OF ORDER'} (seq {seqs[0] if seqs else '-'}..{seqs[-1] if seqs else '-'})")
    ok = ok and saw_down and delivered > 0 and lost == 0 and in_order

    send({"cmd": "speed", "cycle_ms": NORMAL_MS})
    print(f"\nDemo finished: {'all 4 steps shown' if ok else 'a step did not complete, see above'}. "
          "Replay back at 1 reading/s.")
    client.loop_stop()
    client.disconnect()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
