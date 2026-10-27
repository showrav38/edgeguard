"""Unit tests for the alarm state machine (edge_app/alarms.py): every transition, grouping, counters,
and the Acknowledge command. No PLC or broker needed. Time is in seconds, passed in explicitly."""
import json

import pytest

from edge_app import commands
from edge_app.alarms import ACTIVE, COOLDOWN, NORMAL, WAITING_FOR_ACK, AlarmManager, iso

COOL = 60


def reading(alarm_type: str | None = None, risk: float = 0.3, failure: bool = False) -> dict:
    """A telemetry message as far as the alarm machine reads it."""
    return {"state": "ALARM" if alarm_type else "OK", "failure_type": alarm_type or "none", "risk": risk,
            "ground_truth": {"failure": failure, "type": "TWF" if failure else "none"}}


def state(m: AlarmManager, t: str = "power", now: float = 0) -> str:
    return m.snapshot(now)["types"][t]["state"]


def test_normal_to_active_on_alarm():
    m = AlarmManager(0, COOL)
    m.on_reading(reading(), 1)
    assert state(m, now=1) == NORMAL
    m.on_reading(reading("power"), 2)
    s = m.snapshot(2)
    assert s["types"]["power"]["state"] == ACTIVE and s["latched"] and not s["acknowledged"]
    assert s["types"]["power"]["start_ts"] == iso(2)


def test_active_to_waiting_when_readings_clear():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.on_reading(reading(), 2)
    s = m.snapshot(2)["types"]["power"]
    assert s["state"] == WAITING_FOR_ACK and s["end_ts"] == iso(2)  # still latched: nobody has seen it


def test_waiting_back_to_active_is_the_same_alarm():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power", risk=0.3), 1)
    m.on_reading(reading(), 2)
    m.on_reading(reading("power", risk=0.6), 3)
    s = m.snapshot(3)["types"]["power"]
    assert s["state"] == ACTIVE and s["start_ts"] == iso(1)  # same start
    assert s["peak"]["risk"] == 0.6                          # highest-risk reading is shown


def test_waiting_ack_starts_cooldown_then_normal():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.on_reading(reading(), 2)
    assert m.acknowledge(10) == ["power"]
    assert state(m, now=10) == COOLDOWN and m.snapshot(10)["types"]["power"]["cooldown_until"] == iso(70)
    assert state(m, now=69.9) == COOLDOWN
    assert state(m, now=70) == NORMAL  # cool-down over (time alone, no reading needed)


def test_ack_while_active_stays_until_clear_then_cooldown():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.acknowledge(5)
    s = m.snapshot(5)
    assert s["types"]["power"]["state"] == ACTIVE and s["types"]["power"]["acknowledged"]
    assert s["latched"] and s["acknowledged"]  # shown, but no longer stops the robot
    m.on_reading(reading("power"), 6)
    assert state(m, now=6) == ACTIVE          # still the same, acknowledged alarm
    m.on_reading(reading(), 7)
    assert state(m, now=7) == COOLDOWN        # cool-down counted from the acknowledge (5 + 60)
    assert state(m, now=65) == NORMAL


def test_ack_while_active_longer_than_cooldown_goes_to_normal():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.acknowledge(2)
    m.on_reading(reading(), 100)  # the alarm lasted beyond the cool-down
    assert state(m, now=100) == NORMAL


def test_cooldown_suppresses_same_type_but_records_and_counts_it():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.on_reading(reading(), 2)
    m.acknowledge(3)
    assert m.on_reading(reading("power"), 20) is True   # suppressed: not latched again
    s = m.snapshot(20)
    assert s["types"]["power"]["state"] == COOLDOWN and not s["latched"]
    assert s["counters"]["total"] == 2                  # nothing hidden from the counters
    assert s["types"]["power"]["group"]["count"] == 2


def test_cooldown_does_not_hide_a_different_type():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.on_reading(reading(), 2)
    m.acknowledge(3)
    assert m.on_reading(reading("tool_wear"), 10) is False
    s = m.snapshot(10)
    assert s["types"]["tool_wear"]["state"] == ACTIVE and s["latched"] and not s["acknowledged"]


def test_alarm_after_cooldown_latches_again():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.on_reading(reading(), 2)
    m.acknowledge(3)
    m.on_reading(reading(), 62)
    m.on_reading(reading("power"), 63)
    assert state(m, now=63) == ACTIVE


def test_ack_with_nothing_to_acknowledge_is_ignored():
    m = AlarmManager(0, COOL)
    assert m.acknowledge(1) == []
    m.on_reading(reading("power"), 2)
    m.acknowledge(3)
    assert m.acknowledge(4) == []                        # already acknowledged
    assert m.snapshot(4)["types"]["power"]["cooldown_until"] == iso(63)  # not extended


def test_ack_only_for_the_given_type():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 1)
    m.on_reading(reading("tool_wear"), 2)   # power -> WAITING, tool_wear -> ACTIVE
    assert m.acknowledge(3, "power") == ["power"]
    s = m.snapshot(3)
    assert s["types"]["power"]["state"] == COOLDOWN and s["types"]["tool_wear"]["state"] == ACTIVE
    assert not s["acknowledged"]            # tool_wear still needs one


def test_late_ack_cannot_release_an_alarm_the_operator_had_not_seen():
    m = AlarmManager(0, COOL)
    m.on_reading(reading("power"), 5)
    assert m.acknowledge(6, seen_ts=4) == []
    assert m.acknowledge(6, seen_ts=5) == ["power"]


def test_group_and_rate_window():
    m = AlarmManager(0, COOL)
    for t in (10, 100, 200):                  # 3 power alarms < 5 min apart: one group
        m.on_reading(reading("power", failure=t == 10), t)
        m.on_reading(reading(), t + 1)
    s = m.snapshot(201)
    assert s["types"]["power"]["group"] == {"count": 3, "since": iso(10)}
    assert s["counters"] == {"total": 3, "false": 2, "last_10min": 3, "watched_s": 201}
    m.on_reading(reading("power"), 200 + 301)  # gap over 5 min: a new group
    assert m.snapshot(501)["types"]["power"]["group"] == {"count": 1, "since": iso(501)}
    assert m.snapshot(701)["counters"]["last_10min"] == 2  # the alarms at 10 and 100 s left the 10-min window


def test_one_event_per_step_into_alarm():
    m = AlarmManager(0, COOL)
    for t in range(1, 6):
        m.on_reading(reading("power"), t)     # one long alarm = one event
    assert m.snapshot(6)["counters"]["total"] == 1


def test_ack_command_is_validated():
    ok = commands.parse(json.dumps({"cmd": "ack", "type": "power", "ts": "2026-10-27T10:00:05.000+00:00"}).encode())
    assert ok["cmd"] == "ack" and ok["type"] == "power" and ok["seen_ts"] == pytest.approx(1793095205.0)
    assert commands.parse(b'{"cmd": "ack"}') == {"cmd": "ack", "type": None, "seen_ts": None}
    for bad in ({"cmd": "ack", "type": "fire"}, {"cmd": "ack", "ts": "yesterday"}, {"cmd": "ack", "ts": 5}):
        with pytest.raises(ValueError):
            commands.parse(json.dumps(bad).encode())
