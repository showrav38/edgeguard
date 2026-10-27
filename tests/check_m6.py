"""M6 check: the whole stack comes up with `docker compose`, every service is healthy, telemetry reaches MQTT.

  A. `docker compose up -d --build --wait`: builds the images, starts mosquitto -> plc_sim -> edge_app ->
     ros2 + dashboard (each waits for the previous one's healthcheck)
  B. all 5 services report "healthy" (ros2 since M7b)
  C. a telemetry message arrives on edgeguard/machine1/telemetry within 15 s
  D. the dashboard is served by nginx on http://localhost:8080
  E. `docker compose down` (always, also after a failure; volumes are kept)
Prints PASS or FAIL. Run: make check-m6   (stop a locally running `make plc` / `make edge` first: same ports)
"""
import json
import subprocess
import sys
import time
import urllib.request

import paho.mqtt.client as mqtt

SERVICES = ["mosquitto", "plc_sim", "edge_app", "ros2", "dashboard"]
TOPIC_TELEMETRY = "edgeguard/machine1/telemetry"
DASHBOARD_URL = "http://localhost:8080/"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"[{'ok' if ok else 'FAIL'}] {name}  {detail}", flush=True)


def compose(*args: str, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], capture_output=True, text=True, timeout=timeout)


def health() -> dict[str, str]:
    """service -> health ("healthy", "starting", "unhealthy", or "" without a healthcheck)."""
    out = compose("ps", "--all", "--format", "json").stdout.strip()
    # Newer compose prints one JSON object per line, older versions one JSON list.
    rows = json.loads(out) if out.startswith("[") else [json.loads(line) for line in out.splitlines() if line]
    return {r["Service"]: r.get("Health", "") or r.get("State", "") for r in rows}


def first_telemetry(timeout_s: float) -> dict | None:
    got: list[dict] = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-check-m6")
    client.on_connect = lambda c, *_: c.subscribe(TOPIC_TELEMETRY, qos=1)
    client.on_message = lambda c, u, m: got.append(json.loads(m.payload))
    client.connect("localhost", 1883, keepalive=10)
    client.loop_start()
    t_end = time.time() + timeout_s
    while not got and time.time() < t_end:
        time.sleep(0.1)
    client.loop_stop()
    client.disconnect()
    return got[0] if got else None


def main() -> int:
    try:
        t0 = time.time()
        up = compose("up", "-d", "--build", "--wait", "--wait-timeout", "180")
        check("A. docker compose up --wait", up.returncode == 0,
              f"{time.time() - t0:.0f} s" if up.returncode == 0 else up.stderr.strip()[-500:])

        h = health()
        check("B. all services healthy", all(h.get(s) == "healthy" for s in SERVICES),
              ", ".join(f"{s}={h.get(s, 'missing')}" for s in SERVICES))

        m = first_telemetry(15)
        check("C. telemetry reaches MQTT", m is not None,
              f"seq {m['seq']}, state {m['state']}, risk {m['risk']:.3f}"
              if m else "no message in 15 s")

        try:
            with urllib.request.urlopen(DASHBOARD_URL, timeout=5) as r:
                body = r.read().decode()
            check("D. dashboard served on :8080", r.status == 200 and '<div id="root">' in body, f"HTTP {r.status}")
        except OSError as ex:
            check("D. dashboard served on :8080", False, str(ex))
    finally:
        down = compose("down", timeout=120)
        check("E. docker compose down", down.returncode == 0)

    ok = all(passed for _, passed, _ in results)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
