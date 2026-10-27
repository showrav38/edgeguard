"""How often does the robot cell stop? Before vs after D-32 (alarm state owned by the edge app).

Replays the 2,000 test rows in time order at 1 reading/s (as the PLC simulator does at 1x) through the
real models and decision code, without PLC, broker or ROS. A simulated operator presses Acknowledge
ACK_DELAY_S seconds after the robot stops (and again while it stays stopped).
  before: the old cell_guard rule (commit d64f8cd): any ALARM reading latches, Acknowledge releases, no cool-down
  after:  edge_app/alarms.py -> alarm_state -> the new cell_guard rule (stop = latched and not acknowledged)
Counts robot stops (running -> stopped) per 10 min. Run: .venv/bin/python scripts/robot_stops.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "ros2", "edgeguard_ros")]

from edge_app import actions  # noqa: E402
from edge_app.alarms import AlarmManager, iso  # noqa: E402
from edge_app.inference import Models  # noqa: E402
from edgeguard_ros.interlock import Interlock  # noqa: E402
from plc_sim.server import DATA_CSV, ground_truth_type, load_rows, row_values  # noqa: E402

ACK_DELAYS_S = [10, 30]  # how long the operator takes to press Acknowledge


class OldInterlock:
    """The rule before D-32, copied from ros2/.../interlock.py at commit d64f8cd (time in seconds)."""

    def __init__(self):
        self.latched, self.alarm_now, self.hold_off = None, False, False

    @property
    def stop(self):
        return self.latched is not None

    def on_telemetry(self, t, alarm):
        self.alarm_now = alarm
        if not alarm:
            self.hold_off = False
        elif self.latched is None and not self.hold_off:
            self.latched = t

    def on_ack(self, t):
        if self.latched is not None and t >= self.latched:
            self.latched, self.hold_off = None, self.alarm_now


def readings():
    models = Models()
    for row in load_rows(DATA_CSV):
        signals = {k: v for k, v in row_values(row, drift=False).items() if not k.startswith("GroundTruth")}
        pred = models.predict(signals)
        d = actions.decide(pred, signals, models.threshold)
        yield {"state": d["state"], "failure_type": d["failure_type"], "risk": pred["risk"],
               "ground_truth": {"failure": row["Machine failure"] == "1", "type": ground_truth_type(row)}}


def count_stops(msgs: list[dict], new_rule: bool, ack_delay: float) -> int:
    old, am, il = OldInterlock(), AlarmManager(0), Interlock()
    stops, stopped_at, was_stop = 0, None, False
    for t, m in enumerate(msgs, start=1):
        if new_rule:
            am.on_reading(m, t)
            il.on_alarm_state({"ts": iso(t), **am.snapshot(t)})
            stop = il.stop
        else:
            old.on_telemetry(t, m["state"] == "ALARM")
            stop = old.stop
        if stop and not was_stop:
            stops, stopped_at = stops + 1, t
        if stop and t - stopped_at >= ack_delay:  # the operator acknowledges what they see
            if new_rule:
                am.acknowledge(t, None, seen_ts=t)
                il.on_alarm_state({"ts": iso(t + 0.5), **am.snapshot(t)})  # published just after the reading
                stop = il.stop
            else:
                old.on_ack(t)
                stop = old.stop
            stopped_at = t
        was_stop = stop
    return stops


def main():
    msgs = list(readings())
    minutes = len(msgs) / 60
    alarms = sum(1 for a, b in zip([None] + msgs, msgs) if b["state"] == "ALARM" and (a is None or a["state"] != "ALARM"))
    print(f"{len(msgs)} readings at 1/s = {minutes:.0f} min, {alarms} alarms ({alarms / minutes * 10:.1f} per 10 min)")
    print(f"{'operator acknowledges after':<30}{'before (per 10 min)':>22}{'after (per 10 min)':>22}")
    for delay in ACK_DELAYS_S:
        before, after = count_stops(msgs, False, delay), count_stops(msgs, True, delay)
        print(f"{f'{delay} s':<30}{f'{before} stops = {before / minutes * 10:.1f}':>22}"
              f"{f'{after} stops = {after / minutes * 10:.1f}':>22}")


if __name__ == "__main__":
    main()
