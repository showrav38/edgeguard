"""M7b check: the ROS 2 robot cell stops on an EdgeGuard alarm and runs again after Acknowledge.

  A. `docker compose up -d --build --wait`: the whole stack incl. the ros2 service is healthy
  B. start released: acknowledge any old alarm; `ros2 topic echo /robot_cell/state` says "running"
  C. trigger an alarm: replay at 10x until an ALARM reading that latches (not in a cool-down), then back to 1x
  D. `ros2 topic echo /robot_cell/stop` becomes true (the interlock)
  E. robot_sim stops: /robot_cell/state = "stopped by EdgeGuard", and its parts count (MQTT) stays still
  F. two dashboards (two MQTT clients on the retained alarm_state) show the same latched alarm
  G. dashboard A acknowledges (the {"cmd": "ack"} its button sends): both dashboards show it acknowledged,
     /robot_cell/stop becomes false, /robot_cell/state "running" (a new alarm of ANOTHER type in between
     correctly stops the robot again; the check acknowledges again, up to 3 times)
  H. robot_sim loads parts again (count goes up within 10 s)
  I. cool-down (D-32): replay fast; repeats of the acknowledged type arrive (alarm_suppressed) but never make
     that type need an acknowledge again, so they cannot stop the robot
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
alarm_states: list[dict] = []       # every edgeguard/machine1/alarm_state received
robot: dict = {}                    # latest edgeguard/robot_cell/status
FAST_I_MS = 50                      # step I: as fast as the PLC simulator goes


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
        elif m.topic == f"{T}/alarm_state":
            alarm_states.append(json.loads(m.payload))
        else:
            telemetry.append(json.loads(m.payload))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-check-m7b")
    client.on_connect = lambda c, *_: c.subscribe([(f"{T}/telemetry", 1), (TOPIC_ROBOT, 0), (f"{T}/alarm_state", 1)])
    client.on_message = on_message
    client.connect("localhost", 1883, keepalive=10)
    client.loop_start()
    return client


def acknowledge(client: mqtt.Client):
    """What the dashboard's Acknowledge button publishes (type null = every latched type the operator has seen)."""
    ts = telemetry[-1]["ts"] if telemetry else None
    client.publish(f"{T}/cmd", json.dumps({"cmd": "ack", "type": None, "ts": ts}), qos=1)


class Dashboard:
    """A second (and third) dashboard: an MQTT client that only follows the retained alarm_state, like the browser."""

    def __init__(self, name: str):
        self.state: dict | None = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edgeguard-check-m7b-{name}")
        self.client.on_connect = lambda c, *_: c.subscribe(f"{T}/alarm_state", 1)
        self.client.on_message = lambda c, u, m: setattr(self, "state", json.loads(m.payload))
        self.client.connect("localhost", 1883, keepalive=10)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


def stops_in(states: list[dict]) -> int:
    """Robot stops in a sequence of alarm_state messages: steps into "latched and not acknowledged"."""
    stop = [s["latched"] and not s["acknowledged"] for s in states]
    return sum(1 for prev, now in zip([False] + stop, stop) if now and not prev)


def same_state(a: Dashboard, b: Dashboard, want, timeout_s: float = 3) -> bool:
    """Both dashboards hold the identical alarm_state and it satisfies want(state)."""
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        if a.state and a.state == b.state and want(a.state):
            return True
        time.sleep(0.05)
    return False


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

        # C. Replay fast until the model raises an alarm that latches (B's acknowledge started cool-downs).
        n0 = len(telemetry)
        send({"cmd": "speed", "cycle_ms": FAST_MS})
        t_end = time.time() + 60
        alarm = None
        while alarm is None and time.time() < t_end:
            alarm = next((m for m in telemetry[n0:] if m["alarm"] and not m.get("alarm_suppressed")), None)
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

        dash_a, dash_b = Dashboard("a"), Dashboard("b")
        try:
            ok = same_state(dash_a, dash_b, lambda s: s["latched"] and not s["acknowledged"])
            check("F. two dashboards show the same latched alarm", ok,
                  f"A: latched={dash_a.state and dash_a.state['latched']}, B: latched={dash_b.state and dash_b.state['latched']}")
            t_ack = time.time()
            ok, detail = release(dash_a.client)  # Acknowledge from dashboard A
            both = same_state(dash_a, dash_b, lambda s: not s["latched"] or s["acknowledged"])
            check("G. acknowledge in A clears both; stop false, robot running", ok and both,
                  f"{detail}; both dashboards acknowledged: {both}")
        finally:
            dash_a.stop()
            dash_b.stop()

        # One part every 2 s while running; wait up to 10 s for the count to go up.
        before, t_start = robot.get("parts") or 0, time.time()
        while (robot.get("parts") or 0) <= before and time.time() - t_start < 10:
            time.sleep(0.2)
        after = robot.get("parts") or 0
        check("H. robot_sim loads parts again", after > before, f"parts {before} -> {after} in {time.time() - t_start:.1f} s")

        # I. Cool-down: within the 60 s after the acknowledge, repeats of that type must not need an acknowledge.
        ftype = alarm["failure_type"]
        cooldown_end = t_ack + 55  # margin to the edge app's 60 s
        n0, s0 = len(telemetry), len(alarm_states)
        send({"cmd": "speed", "cycle_ms": FAST_I_MS})
        repeats: list[dict] = []
        while not repeats and time.time() < cooldown_end:
            repeats = [m for m in telemetry[n0:] if m["alarm"] and m["failure_type"] == ftype and m.get("alarm_suppressed")]
            time.sleep(0.1)
        send({"cmd": "speed", "cycle_ms": NORMAL_MS})
        time.sleep(0.5)
        t_in = alarm_states[s0:]
        relatched = [s["ts"] for s in t_in if s["types"][ftype]["state"] == "WAITING_FOR_ACK"
                     or (s["types"][ftype]["state"] == "ACTIVE" and not s["types"][ftype]["acknowledged"])]
        check(f"I. cool-down: {ftype} repeats recorded, robot not stopped for {ftype}", bool(repeats) and not relatched,
              f"{len(repeats)} suppressed {ftype} alarm(s) in {time.time() - t_ack:.0f} s after the acknowledge; "
              f"{len(relatched)} alarm_state(s) with {ftype} needing an acknowledge; "
              f"{stops_in(t_in)} stop(s) by other types (correct: a different failure is never hidden)")
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
