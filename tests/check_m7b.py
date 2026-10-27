"""M7b check: the ROS 2 robot cell stops on an EdgeGuard alarm and runs again after Acknowledge.

  A. `docker compose up -d --build --wait`: the whole stack incl. the ros2 service is healthy
  B. start released: acknowledge any old alarm; `ros2 topic echo /robot_cell/state` says "running"
  C. trigger an alarm: replay at 10x until an ALARM reading arrives on MQTT, then back to 1x
  D. `ros2 topic echo /robot_cell/stop` becomes true (the interlock)
  E. robot_sim stops: /robot_cell/state = "stopped by EdgeGuard", and its parts count (MQTT) stays still
  F. acknowledge (as the dashboard button does): /robot_cell/stop becomes false, /robot_cell/state "running"
     (at 1x the replay gives a new alarm about every 12 s; if one comes in between, the interlock correctly
     stops the robot again, and the check acknowledges again, up to 3 times)
  G. robot_sim loads parts again (count goes up within 10 s)
The stack is left running (open http://localhost:8080, Engineer view). `make down` stops it.
Prints PASS or FAIL. Run: make check-m7b
"""
import json
import subprocess
import sys
import time

import paho.mqtt.client as mqtt

T = "edgeguard/machine1"
TOPIC_ROBOT = "edgeguard/robot_cell/status"
FAST_MS, NORMAL_MS = 100, 1000

results: list[tuple[str, bool, str]] = []
telemetry: list[dict] = []          # every reading received during the check
robot: dict = {}                    # latest edgeguard/robot_cell/status


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"[{'ok' if ok else 'FAIL'}] {name}  {detail}", flush=True)


def compose(*args: str, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], capture_output=True, text=True, timeout=timeout)


def ros_echo(topic: str, msg_type: str) -> str:
    """One message from a ROS 2 topic, read with the ros2 CLI inside the ros2 container ("" if none in 5 s)."""
    cmd = f"source /opt/ros/jazzy/setup.bash && timeout 5 ros2 topic echo --once --field data {topic} {msg_type}"
    out = compose("exec", "-T", "ros2", "bash", "-c", cmd, timeout=30).stdout.strip()
    return out.splitlines()[0].strip() if out else ""


def wait_ros(topic: str, msg_type: str, want: str, timeout_s: float) -> tuple[bool, str]:
    """Echo the topic until it shows `want` (case-insensitive). Returns (ok, last value seen)."""
    t_end, value = time.time() + timeout_s, ""
    while time.time() < t_end:
        value = ros_echo(topic, msg_type)
        if value.lower() == want.lower():
            return True, value
    return False, value


def mqtt_client() -> mqtt.Client:
    def on_message(c, userdata, m):
        if m.topic == TOPIC_ROBOT:
            robot.update(json.loads(m.payload))
        else:
            telemetry.append(json.loads(m.payload))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-check-m7b")
    client.on_connect = lambda c, *_: c.subscribe([(f"{T}/telemetry", 1), (TOPIC_ROBOT, 0)])
    client.on_message = on_message
    client.connect("localhost", 1883, keepalive=10)
    client.loop_start()
    return client


def acknowledge(client: mqtt.Client):
    """What the dashboard's Acknowledge button publishes: the time of the newest reading the operator has seen."""
    ts = telemetry[-1]["ts"] if telemetry else None
    client.publish(f"{T}/ack", json.dumps({"ts": ts, "seq": telemetry[-1]["seq"] if telemetry else None}), qos=1)


def parts_after(seconds: float) -> tuple[int, int]:
    before = robot.get("parts") or 0
    time.sleep(seconds)
    return before, robot.get("parts") or 0


def release(client: mqtt.Client, tries: int = 3) -> tuple[bool, str]:
    """Acknowledge until the robot runs (a new alarm in between stops it again, correctly)."""
    for attempt in range(1, tries + 1):
        acknowledge(client)
        stop_ok, stop = wait_ros("/robot_cell/stop", "std_msgs/msg/Bool", "false", 8)
        state_ok, state = wait_ros("/robot_cell/state", "std_msgs/msg/String", "running", 6)
        if stop_ok and state_ok:
            return True, f"stop={stop}, state={state}, after {attempt} acknowledge(s)"
    return False, f"stop={stop}, state={state} after {tries} acknowledges"


def main() -> int:
    t0 = time.time()
    up = compose("up", "-d", "--build", "--wait", "--wait-timeout", "240")
    check("A. docker compose up --wait (incl. ros2)", up.returncode == 0,
          f"{time.time() - t0:.0f} s" if up.returncode == 0 else up.stderr.strip()[-500:])
    if up.returncode != 0:
        return report()

    client = mqtt_client()
    send = lambda cmd: client.publish(f"{T}/cmd", json.dumps(cmd), qos=1)  # noqa: E731
    try:
        send({"cmd": "drift", "on": False})
        send({"cmd": "speed", "cycle_ms": NORMAL_MS})
        time.sleep(2)  # first readings + retained robot status
        ok, detail = release(client)
        check("B. start released: robot running", ok, detail)

        # C. Replay fast until the model raises an alarm (about 1 reading in 12 is an ALARM on this replay).
        n0 = len(telemetry)
        send({"cmd": "speed", "cycle_ms": FAST_MS})
        t_end = time.time() + 60
        alarm = None
        while alarm is None and time.time() < t_end:
            alarm = next((m for m in telemetry[n0:] if m["alarm"]), None)
            time.sleep(0.1)
        send({"cmd": "speed", "cycle_ms": NORMAL_MS})
        check("C. alarm triggered", alarm is not None,
              f"seq {alarm['seq']}, {alarm['failure_type']}, risk {alarm['risk']:.2f}" if alarm else "no ALARM in 60 s")

        ok, value = wait_ros("/robot_cell/stop", "std_msgs/msg/Bool", "true", 10)
        check("D. ros2 topic echo /robot_cell/stop -> true", ok, f"data: {value or 'no message'}")

        ok, state = wait_ros("/robot_cell/state", "std_msgs/msg/String", "stopped by EdgeGuard", 6)
        before, after = parts_after(3)
        check("E. robot_sim stopped, no parts loaded", ok and before == after and robot.get("state") == "stopped",
              f"state: {state}; MQTT {robot.get('state')}; parts {before} -> {after} in 3 s")

        ok, detail = release(client)
        check("F. acknowledge -> stop false, robot running", ok, detail)

        # One part every 2 s while running; wait up to 10 s for the count to go up.
        before, t_start = robot.get("parts") or 0, time.time()
        while (robot.get("parts") or 0) <= before and time.time() - t_start < 10:
            time.sleep(0.2)
        after = robot.get("parts") or 0
        check("G. robot_sim loads parts again", after > before, f"parts {before} -> {after} in {time.time() - t_start:.1f} s")
    finally:
        send({"cmd": "speed", "cycle_ms": NORMAL_MS})
        client.loop_stop()
        client.disconnect()
    return report()


def report() -> int:
    ok = all(r[1] for r in results)
    print(f"\nM7b check: {'PASS' if ok else 'FAIL'}  ({sum(r[1] for r in results)}/{len(results)} checks)")
    print("The stack is still running: http://localhost:8080 (Engineer view, Robot cell card). `make down` stops it.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
