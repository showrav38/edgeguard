"""The interlock rule of cell_guard, without any ROS or MQTT code (so pytest can test it on the host).

Rule: the robot cell must stop while an alarm is latched and not acknowledged.
  - An ALARM reading latches the interlock. It stays on even if the next readings are OK:
    a person must look at the machine before the robot loads it again.
  - Acknowledge (from the dashboard) releases it. If the alarm is still active at that moment,
    it does not latch again until the readings have left ALARM once (same as the dashboard).
Readings are ordered by `ts` (ISO UTC time set by the edge app, so text order = time order; unlike
`seq` it does not restart at 0 when the PLC simulator restarts). An acknowledge carries the `ts` of
the newest reading the operator had seen, so a late acknowledge cannot release a newer alarm, and an
older reading resent by store-and-forward cannot latch again.
No cool-down here (unlike the dashboard, D-28): a safety function must not hide an alarm.
"""


class Interlock:
    def __init__(self):
        self.latched_ts: str | None = None  # time of the reading that latched the alarm
        self.alarm_now = False              # the newest reading is an ALARM
        self.hold_off = False               # acknowledged while active: wait until the alarm clears
        self.last_ts = ""                   # newest reading seen; older or repeated ones are ignored

    @property
    def stop(self) -> bool:
        """True = the robot cell must stop."""
        return self.latched_ts is not None

    def on_telemetry(self, ts: str, alarm: bool):
        if ts <= self.last_ts:
            return  # duplicate or older reading (store-and-forward may resend a message)
        self.last_ts = ts
        self.alarm_now = alarm
        if not alarm:
            self.hold_off = False
        elif self.latched_ts is None and not self.hold_off:
            self.latched_ts = ts

    def on_ack(self, ts: str | None):
        """ts = newest reading the operator had seen when pressing Acknowledge (None = release any alarm)."""
        if self.latched_ts is None:
            return
        if ts is None or ts >= self.latched_ts:
            self.latched_ts = None
            self.hold_off = self.alarm_now
