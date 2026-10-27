"""Unit tests for the robot cell interlock rule (ros2/edgeguard_ros/edgeguard_ros/interlock.py).
The rule has no ROS or MQTT code, so it runs here without a ROS installation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "edgeguard_ros"))
from edgeguard_ros.interlock import Interlock  # noqa: E402


def t(sec: int) -> str:
    """An edge-app style timestamp; text order = time order."""
    return f"2026-10-27T10:00:{sec:02d}.000+00:00"


def test_alarm_latches_until_acknowledged():
    i = Interlock()
    i.on_telemetry(t(1), False)
    assert not i.stop
    i.on_telemetry(t(2), True)
    i.on_telemetry(t(3), False)        # readings back to OK: still stopped (latched)
    assert i.stop
    i.on_ack(t(3))
    assert not i.stop


def test_ack_while_active_does_not_relatch_until_clear():
    i = Interlock()
    i.on_telemetry(t(1), True)
    i.on_ack(t(1))
    i.on_telemetry(t(2), True)         # same alarm still active: already acknowledged
    assert not i.stop
    i.on_telemetry(t(3), False)
    i.on_telemetry(t(4), True)         # a new alarm after the readings cleared: stop again
    assert i.stop


def test_late_ack_cannot_release_newer_alarm():
    i = Interlock()
    i.on_telemetry(t(5), True)
    i.on_ack(t(4))                     # the operator had not seen this alarm yet
    assert i.stop


def test_resent_old_reading_is_ignored():
    i = Interlock()
    i.on_telemetry(t(2), True)
    i.on_ack(t(2))
    i.on_telemetry(t(3), False)
    i.on_telemetry(t(2), True)         # store-and-forward resends an older message
    assert not i.stop
