"""Alarm state for Machine 1: the ONE place that decides latch, acknowledge, cool-down and grouping.

Before (D-22, D-28) every browser kept its own latch and cool-down, so two dashboards could disagree and
the ROS 2 interlock had to repeat the rule without the cool-down. Now the edge app runs this state
machine and publishes the result (retained, edgeguard/machine1/alarm_state); dashboards and cell_guard
only display / follow it (D-32).

One state machine per failure type (tool_wear, heat_dissipation, power, overstrain):

  NORMAL           --ALARM reading of this type-->            ACTIVE          (latched, not acknowledged)
  ACTIVE           --reading leaves ALARM of this type-->     WAITING_FOR_ACK (or COOLDOWN if acknowledged)
  ACTIVE           --Acknowledge-->                           ACTIVE, acknowledged; the cool-down starts now
  WAITING_FOR_ACK  --ALARM again-->                           ACTIVE          (same latched alarm, same start)
  WAITING_FOR_ACK  --Acknowledge-->                           COOLDOWN        (until now + COOLDOWN_S)
  COOLDOWN         --ALARM reading-->                         COOLDOWN        (recorded + counted, not latched)
  COOLDOWN         --time >= cooldown_until-->                NORMAL

Nothing is hidden: every step into ALARM is counted (total, false share, last 10 min) and grouped
("N alarms in the last X min", a gap over 5 min starts a new group), also during the cool-down.
Time = the edge app's clock (seconds since epoch), passed in by the caller so tests can control it.
"""
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

NORMAL, ACTIVE, WAITING_FOR_ACK, COOLDOWN = "NORMAL", "ACTIVE", "WAITING_FOR_ACK", "COOLDOWN"
FAILURE_TYPES = ["tool_wear", "heat_dissipation", "power", "overstrain"]

COOLDOWN_S = float(os.getenv("ACK_COOLDOWN_S", "60"))
GROUP_GAP_S = 5 * 60    # alarms of one type less than 5 min apart are one group
RATE_WINDOW_S = 10 * 60  # ISA-18.2: more than 10 alarms in 10 min is an alarm flood


def iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class TypeAlarm:
    """State of one failure type."""
    state: str = NORMAL
    start: float | None = None          # first ALARM reading of the latched alarm
    end: float | None = None            # first reading after it (None while ACTIVE)
    peak: dict | None = None            # highest-risk reading of the latched alarm (its action is shown)
    acknowledged: bool = False          # only in ACTIVE: acknowledged while the condition is still there
    cooldown_until: float | None = None
    in_alarm: bool = False              # the newest reading is an ALARM of this type
    group_count: int = 0                # alarms in the current group
    group_since: float | None = None
    last_alarm: float | None = None     # start of the newest alarm of this type

    @property
    def needs_ack(self) -> bool:
        """Latched and not acknowledged: what stops the robot cell."""
        return self.state == WAITING_FOR_ACK or (self.state == ACTIVE and not self.acknowledged)


class AlarmManager:
    def __init__(self, now: float, cooldown_s: float = COOLDOWN_S):
        self.cooldown_s = cooldown_s
        self.types = {t: TypeAlarm() for t in FAILURE_TYPES}
        self.started = now
        self.total = 0                  # every step into ALARM, also during the cool-down
        self.false = 0                  # ... of which no real failure happened (replay ground truth)
        self.recent: deque[float] = deque()  # alarm start times within RATE_WINDOW_S

    def _expire(self, now: float):
        for a in self.types.values():
            if a.state == COOLDOWN and now >= a.cooldown_until:
                a.state, a.cooldown_until = NORMAL, None
        while self.recent and self.recent[0] <= now - RATE_WINDOW_S:
            self.recent.popleft()

    def on_reading(self, msg: dict, now: float) -> bool:
        """Update every type with one telemetry reading. Returns True if this reading's alarm is
        suppressed by a cool-down (recorded and counted, but not latched)."""
        self._expire(now)
        alarm_type = msg["failure_type"] if msg["state"] == "ALARM" else None
        suppressed = False
        for name, a in self.types.items():
            here = name == alarm_type
            if here and not a.in_alarm:  # a new alarm EVENT: the step into ALARM, not every reading
                self._count(a, msg, now)
            was_in_alarm, a.in_alarm = a.in_alarm, here
            if here:
                if a.state == NORMAL:
                    a.state, a.start, a.end, a.peak, a.acknowledged = ACTIVE, now, None, msg, False
                elif a.state == WAITING_FOR_ACK:
                    a.state, a.end = ACTIVE, None  # came back before anyone acknowledged it
                elif a.state == COOLDOWN:
                    suppressed = True
                if a.state == ACTIVE and msg["risk"] > a.peak["risk"]:
                    a.peak = msg
            elif a.state == ACTIVE and was_in_alarm:
                a.end = now
                if not a.acknowledged:
                    a.state = WAITING_FOR_ACK
                else:
                    a.state = COOLDOWN if now < a.cooldown_until else NORMAL
        return suppressed

    def _count(self, a: TypeAlarm, msg: dict, now: float):
        self.total += 1
        self.false += 0 if msg["ground_truth"]["failure"] else 1
        self.recent.append(now)
        if a.last_alarm is None or now - a.last_alarm > GROUP_GAP_S:
            a.group_count, a.group_since = 0, now
        a.group_count += 1
        a.last_alarm = now

    def acknowledge(self, now: float, failure_type: str | None = None, seen_ts: float | None = None) -> list[str]:
        """Acknowledge the alarms waiting for it (one type, or all if None). `seen_ts` = time of the newest
        reading the operator had seen: an alarm that started later is not acknowledged (they did not see it).
        Returns the acknowledged types; empty = nothing to acknowledge (the command is ignored)."""
        self._expire(now)
        done = []
        for name, a in self.types.items():
            if failure_type not in (None, name) or not a.needs_ack:
                continue
            if seen_ts is not None and seen_ts < a.start:
                continue
            a.cooldown_until = now + self.cooldown_s
            if a.state == ACTIVE:
                a.acknowledged = True  # stays shown until the readings leave ALARM
            else:
                a.state = COOLDOWN
            done.append(name)
        return done

    def snapshot(self, now: float) -> dict:
        """The retained alarm_state message (JSON-ready)."""
        self._expire(now)
        types = {}
        for name, a in self.types.items():
            types[name] = {
                "state": a.state,
                "acknowledged": a.acknowledged if a.state == ACTIVE else a.state == COOLDOWN,
                "start_ts": iso(a.start) if a.state in (ACTIVE, WAITING_FOR_ACK) else None,
                "end_ts": iso(a.end) if a.state == WAITING_FOR_ACK else None,
                "peak": a.peak if a.state in (ACTIVE, WAITING_FOR_ACK) else None,
                "cooldown_until": iso(a.cooldown_until),
                "group": {"count": a.group_count, "since": iso(a.group_since)},
            }
        latched = any(a.state in (ACTIVE, WAITING_FOR_ACK) for a in self.types.values())
        return {
            # stop the robot cell = latched and not acknowledged (= some type needs an acknowledge)
            "latched": latched,
            "acknowledged": latched and not any(a.needs_ack for a in self.types.values()),
            "active": any(a.state == ACTIVE for a in self.types.values()),  # an ALARM reading right now
            "types": types,
            "counters": {"total": self.total, "false": self.false, "last_10min": len(self.recent),
                         "watched_s": round(min(RATE_WINDOW_S, now - self.started), 1)},
            "cooldown_s": self.cooldown_s,
        }
