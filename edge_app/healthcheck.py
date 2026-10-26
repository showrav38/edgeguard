"""Docker healthcheck for the edge app: healthy = a fresh "running" status heartbeat with the PLC connected.

The edge app publishes edgeguard/machine1/status (retained) every second (main.py). We read the retained
message and check that it is recent, so a dead edge app (stale heartbeat or its "offline" last will)
counts as unhealthy. Exit code 0 = healthy, 1 = unhealthy (what Docker expects).
Run: python -m edge_app.healthcheck
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_STATUS = os.getenv("MQTT_TOPIC_STATUS", "edgeguard/machine1/status")
MAX_AGE_S = 5.0


def main() -> int:
    got: list[dict] = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)  # random client id: never kicks the edge app off
    client.on_connect = lambda c, *_: c.subscribe(TOPIC_STATUS)
    client.on_message = lambda c, u, m: got.append(json.loads(m.payload))
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=5)
    client.loop_start()
    deadline = time.time() + 3
    while not got and time.time() < deadline:
        time.sleep(0.05)
    client.loop_stop()
    client.disconnect()
    if not got:
        print("no status message")
        return 1
    s = got[0]
    if s.get("state") != "running" or "ts" not in s:
        print(f"state is {s.get('state')!r}")
        return 1
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(s["ts"])).total_seconds()
    if age > MAX_AGE_S:
        print(f"status is {age:.1f} s old")
        return 1
    if not s["plc"]["connected"]:
        print("PLC not connected")
        return 1
    print(f"ok: seq {s['plc']['last_seq']}, uplink {s['uplink']['state']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
