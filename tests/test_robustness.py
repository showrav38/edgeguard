"""Unit tests for M4/M5: drift monitor, SQLite outbox, store-and-forward, demo commands
(no PLC or broker needed)."""
import json
import os
import time

import pandas as pd
import pytest

from edge_app import commands
from edge_app.buffer import Outbox, StoreAndForward
from edge_app.drift import DRIFT_WINDOW, DriftMonitor, thresholds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFO = json.load(open(os.path.join(ROOT, "models", "model_info.json")))
TEST = pd.read_csv(os.path.join(ROOT, "data", "test.csv"))
DRIFT_ON, DRIFT_OFF = thresholds(INFO)


def rows(n, temp_shift=0.0, speed_shift=0.0, start=0):
    for _, r in TEST.iloc[start:start + n].iterrows():
        yield {"AirTempK": r.AirTempK + temp_shift, "ProcessTempK": r.ProcessTempK + temp_shift,
               "SpeedRpm": r.SpeedRpm + speed_shift, "TorqueNm": r.TorqueNm, "ToolWearMin": r.ToolWearMin}


def test_thresholds_were_chosen_without_test_data():
    d = INFO["drift"]
    assert d["chosen_on"].startswith("train + validation")
    assert d["calibration"]["false_warning_rate"] <= d["target_false_rate"]


def test_few_false_warnings_on_train_and_validation_rows_in_time_order():
    # Same measurement as training/calibrate_drift.py, done here independently (UDI 1-8000).
    past = pd.concat([pd.read_csv(os.path.join(ROOT, "data", f"{n}.csv")) for n in ("train", "val")])
    mon = DriftMonitor(INFO["training_stats"], DRIFT_ON, DRIFT_OFF)
    results = [mon.update(r) for r in past.sort_values("UDI").to_dict("records")]
    assert results[0]["score"] is None                  # window not full yet
    scored = [r["warning"] for r in results if r["score"] is not None]
    assert sum(scored) / len(scored) <= INFO["drift"]["target_false_rate"]


def test_drift_mode_shift_is_detected_quickly_and_clears():
    mon = DriftMonitor(INFO["training_stats"], DRIFT_ON, DRIFT_OFF)
    for s in rows(100):
        mon.update(s)
    # Same shift as the PLC drift mode: +8 K on both temperatures, +300 rpm.
    delay = next(i for i, s in enumerate(rows(100, 8, 300, start=100), 1) if mon.update(s)["warning"])
    assert delay <= DRIFT_WINDOW
    # Process temperature has the smallest training std, so the same +8 K stands out most there.
    assert "ProcessTempK" in mon.update(next(rows(1, 8, 300, start=300)))["signals"]
    # Back to normal: the warning clears once the window is mostly normal again.
    cleared = [mon.update(s)["warning"] for s in rows(100, start=400)]
    assert cleared[-1] is False


def test_tool_wear_is_not_drift():
    mon = DriftMonitor(INFO["training_stats"], DRIFT_ON, DRIFT_OFF)
    results = [mon.update({**s, "ToolWearMin": 230.0}) for s in rows(200)]
    assert not any(r["warning"] for r in results)


def test_outbox_keeps_order_and_survives_restart(tmp_path):
    path = str(tmp_path / "outbox.db")
    box = Outbox(path)
    for i in range(5):
        box.put("t", json.dumps({"seq": i}))
    box.delete(box.oldest()[0])
    box.db.close()

    box = Outbox(path)  # "restart": same file
    assert len(box) == 4
    seen = []
    while (row := box.oldest()) is not None:
        seen.append(json.loads(row[2])["seq"])
        box.delete(row[0])
    assert seen == [1, 2, 3, 4]


def test_outbox_drops_oldest_when_full(tmp_path):
    box = Outbox(str(tmp_path / "outbox.db"), max_rows=3)
    for i in range(5):
        box.put("t", json.dumps({"seq": i}))
    assert len(box) == 3 and box.dropped == 2
    assert json.loads(box.oldest()[2])["seq"] == 2


def test_outbox_dropped_count_survives_restart(tmp_path):
    path = str(tmp_path / "outbox.db")
    box = Outbox(path, max_rows=2)
    for i in range(5):
        box.put("t", json.dumps({"seq": i}))
    box.db.close()
    box = Outbox(path, max_rows=2)  # "restart": the count is read back from SQLite
    assert box.dropped == 3
    box.put("t", json.dumps({"seq": 5}))
    assert box.dropped == 4 and len(box) == 2


class FakeMqtt:
    """Stands in for paho: always connected, acknowledges every publish at once."""

    def __init__(self):
        self.sent, self.mid, self.on_publish = [], 0, None

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos=0):
        self.mid += 1
        self.sent.append(json.loads(payload))
        info = type("Info", (), {"mid": self.mid})()
        self.on_publish(self, None, self.mid, None, None)  # PUBACK
        return info


def wait_for(cond, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not cond():
        time.sleep(0.02)
    return cond()


def test_simulated_outage_buffers_and_reports_dropped(tmp_path):
    client = FakeMqtt()
    sender = StoreAndForward(client, Outbox(str(tmp_path / "outbox.db"), max_rows=3))
    try:
        sender.send("t", {"seq": 1})
        assert wait_for(lambda: len(client.sent) == 1)
        assert client.sent[0]["buffered"] is False and sender.uplink_state() == "up"

        sender.simulate_outage(0.5)  # broker reachable, but the uplink is treated as down
        assert sender.uplink_state() == "simulated_down"
        for seq in range(2, 7):      # 5 messages into a buffer of 3: the 2 oldest are dropped
            sender.send("t", {"seq": seq})
        assert len(client.sent) == 1  # nothing sent during the outage

        assert wait_for(lambda: len(client.sent) == 4)  # drained after the outage, in order
        assert [m["seq"] for m in client.sent] == [1, 4, 5, 6]
        assert all(m["buffered"] for m in client.sent[1:])
        # A message carries the count from BEFORE it was stored: seq 6 pushed out the 2nd drop.
        assert client.sent[-1]["buffer_dropped"] == 1 and sender.outbox.dropped == 2
        assert sender.uplink_state() == "up" and sender.last_ack_ms is not None
    finally:
        sender.stop()


@pytest.mark.parametrize("payload, expected", [
    (b'{"cmd": "drift", "on": true}', {"cmd": "drift", "on": True}),
    (b'{"cmd": "outage", "seconds": 20}', {"cmd": "outage", "seconds": 20.0}),
    (b'{"cmd": "speed", "cycle_ms": 100}', {"cmd": "speed", "cycle_ms": 100}),
])
def test_commands_valid(payload, expected):
    assert commands.parse(payload) == expected


@pytest.mark.parametrize("payload", [
    b"not json", b"[1, 2]", b'{"cmd": "reboot"}', b'{"cmd": "drift", "on": 1}',
    b'{"cmd": "outage", "seconds": 9999}', b'{"cmd": "speed", "cycle_ms": 5}',
    b'{"cmd": "speed", "cycle_ms": true}',
])
def test_commands_rejected(payload):
    with pytest.raises(ValueError):
        commands.parse(payload)
