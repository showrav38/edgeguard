"""Unit tests for M4: drift monitor and the SQLite outbox (no PLC or broker needed)."""
import json
import os

import pandas as pd

from edge_app.buffer import Outbox
from edge_app.drift import DriftMonitor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFO = json.load(open(os.path.join(ROOT, "models", "model_info.json")))
TEST = pd.read_csv(os.path.join(ROOT, "data", "test.csv"))


def rows(n, temp_shift=0.0, speed_shift=0.0, start=0):
    for _, r in TEST.iloc[start:start + n].iterrows():
        yield {"AirTempK": r.AirTempK + temp_shift, "ProcessTempK": r.ProcessTempK + temp_shift,
               "SpeedRpm": r.SpeedRpm + speed_shift, "TorqueNm": r.TorqueNm, "ToolWearMin": r.ToolWearMin}


def test_no_drift_warning_on_unseen_test_rows():
    mon = DriftMonitor(INFO["training_stats"])
    results = [mon.update(s) for s in rows(len(TEST))]
    assert results[0]["score"] is None                  # window not full yet
    assert not any(r["warning"] for r in results)
    assert max(r["score"] for r in results if r["score"] is not None) < 0.75


def test_drift_mode_shift_is_detected_quickly_and_clears():
    mon = DriftMonitor(INFO["training_stats"])
    for s in rows(100):
        mon.update(s)
    # Same shift as the PLC drift mode: +8 K on both temperatures, +300 rpm.
    delay = next(i for i, s in enumerate(rows(100, 8, 300, start=100), 1) if mon.update(s)["warning"])
    assert delay <= 25
    assert "AirTempK" in mon.update(next(rows(1, 8, 300, start=300)))["signals"]
    # Back to normal: the warning clears once the window is mostly normal again.
    cleared = [mon.update(s)["warning"] for s in rows(100, start=400)]
    assert cleared[-1] is False


def test_tool_wear_is_not_drift():
    mon = DriftMonitor(INFO["training_stats"])
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
