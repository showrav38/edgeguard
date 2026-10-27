"""Unit tests for the robot cell interlock rule (ros2/edgeguard_ros/edgeguard_ros/interlock.py), alone and fed
by the edge app's alarm state machine (edge_app/alarms.py) exactly as over MQTT: snapshot -> JSON -> Interlock.
The rule has no ROS or MQTT code, so it runs here without a ROS installation."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "edgeguard_ros"))
from edgeguard_ros.interlock import Interlock  # noqa: E402

from edge_app.alarms import AlarmManager, iso  # noqa: E402


def t(sec: int) -> str:
    """An edge-app style timestamp; text order = time order."""
    return f"2026-10-27T10:00:{sec:02d}.000+00:00"


def test_stop_only_while_latched_and_not_acknowledged():
    i = Interlock()
    i.on_alarm_state({"ts": t(1), "latched": False, "acknowledged": False, "active": False})
    assert not i.stop
    i.on_alarm_state({"ts": t(2), "latched": True, "acknowledged": False, "active": True})
    assert i.stop and i.alarm_now
    i.on_alarm_state({"ts": t(3), "latched": True, "acknowledged": True, "active": True})
    assert not i.stop and i.alarm_now   # acknowledged, still active: the robot may run


def test_older_alarm_state_is_ignored():
    i = Interlock()
    i.on_alarm_state({"ts": t(5), "latched": False, "acknowledged": False, "active": False})
    i.on_alarm_state({"ts": t(4), "latched": True, "acknowledged": False, "active": True})  # late duplicate
    assert not i.stop


# ---------- fed by the real state machine, as cell_guard is over MQTT ----------
def reading(alarm_type=None):
    return {"state": "ALARM" if alarm_type else "OK", "failure_type": alarm_type or "none", "risk": 0.3,
            "ground_truth": {"failure": False, "type": "none"}}


class Cell:
    """AlarmManager (edge app) -> alarm_state JSON (MQTT) -> Interlock (cell_guard). Counts robot stops."""

    def __init__(self):
        self.edge, self.guard, self.stops = AlarmManager(0, cooldown_s=60), Interlock(), 0

    def publish(self, now: float):
        was = self.guard.stop
        self.guard.on_alarm_state(json.loads(json.dumps({"ts": iso(now), **self.edge.snapshot(now)})))
        self.stops += self.guard.stop and not was

    def read(self, now: float, alarm_type=None):
        self.edge.on_reading(reading(alarm_type), now)
        self.publish(now)

    def ack(self, now: float):
        self.edge.acknowledge(now)
        self.publish(now)


def test_alarm_latches_until_acknowledged():
    c = Cell()
    c.read(1, "power")
    c.read(2)                          # readings back to OK: still stopped (latched)
    assert c.guard.stop
    c.ack(3)
    assert not c.guard.stop


def test_robot_does_not_stop_again_for_the_same_type_during_cooldown():
    c = Cell()
    c.read(1, "power")
    c.read(2)
    c.ack(3)                           # cool-down for power until 63 s
    for sec in range(10, 60, 12):      # v1's flood: a power alarm every ~12 s
        c.read(sec, "power")
        c.read(sec + 1)
        assert not c.guard.stop
    assert c.stops == 1
    c.read(70, "power")                # after the cool-down: stops again
    assert c.guard.stop and c.stops == 2


def test_a_different_type_stops_the_robot_during_the_cooldown():
    c = Cell()
    c.read(1, "power")
    c.ack(2)
    c.read(3)
    c.read(10, "tool_wear")            # a new, different failure is never hidden
    assert c.guard.stop and c.stops == 2


def test_two_clients_see_the_same_state_and_one_ack_clears_both():
    """Two subscribers to the retained alarm_state (like two dashboards) always agree."""
    c, other = Cell(), Interlock()
    c.read(1, "power")
    c.read(2)
    other.on_alarm_state({"ts": iso(2), **c.edge.snapshot(2)})
    assert c.guard.stop and other.stop
    c.ack(3)                           # Acknowledge from one client
    other.on_alarm_state({"ts": iso(3), **c.edge.snapshot(3)})
    assert not c.guard.stop and not other.stop
