"""The interlock rule of cell_guard, without any ROS or MQTT code (so pytest can test it on the host).

Rule: the robot cell must stop while an alarm is latched and not acknowledged.
The edge app decides what is latched, acknowledged and in cool-down (edge_app/alarms.py, D-32) and
publishes it retained on edgeguard/machine1/alarm_state. This node only follows that message, so the
robot, the PLC and every dashboard see the same state, and the robot respects the 60 s cool-down:
a repeat of an acknowledged failure type within the cool-down does not stop it again; a different
type does. (Before D-32 this file had its own latch without a cool-down: one stop every ~12 s.)
Messages are ordered by `ts` (edge app's clock); an older or repeated one is ignored.
"""


class Interlock:
    def __init__(self):
        self.stop = False       # True = the robot cell must stop
        self.alarm_now = False  # an alarm is active right now (the newest reading is an ALARM)
        self.last_ts = ""       # newest alarm_state seen

    def on_alarm_state(self, msg: dict):
        ts = msg["ts"]
        if ts <= self.last_ts:
            return  # duplicate or older message
        self.last_ts = ts
        self.stop = bool(msg["latched"]) and not bool(msg["acknowledged"])
        self.alarm_now = bool(msg["active"])
