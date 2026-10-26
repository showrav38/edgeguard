"""Choose the drift warning thresholds on training + validation (UDI 1-8000), report them on test.

The drift monitor (edge_app/drift.py) scores a rolling window of readings; the warning turns on at
DRIFT_ON and off below DRIFT_OFF. Here we replay the validation rows in time order through the SAME
DriftMonitor class the edge app uses and pick the smallest DRIFT_ON whose false-warning rate is at
most TARGET_FALSE_RATE (smallest = fastest detection). DRIFT_OFF = 0.8 x DRIFT_ON (hysteresis).
Why train + validation: together they cover both the hot and the calm season (UDI 1-8000 is one
continuous stretch of time). Validation alone is one calm season: too thin a margin (D-21).

False-warning rate = share of normal readings (with a full window) during which the warning is on.
Detection delay  = readings from switching drift mode on (+8 K, +300 rpm, same as the PLC simulator)
                   until the warning, measured at many start points.
The test period is only REPORTED, never used for the choice.

Outputs: models/model_info.json -> "drift"
"""
import os
import sys

import numpy as np
import pandas as pd

import common as c

sys.path.insert(0, c.ROOT)  # use the edge app's own detector, not a copy of it
from edge_app.drift import DRIFT_WINDOW, DriftMonitor  # noqa: E402

TARGET_FALSE_RATE = float(os.getenv("DRIFT_TARGET_FALSE_RATE", "0.01"))
OFF_RATIO = 0.8
# Same shift as the PLC simulator's drift mode (plc_sim/server.py: DRIFT_TEMP_K, DRIFT_SPEED_RPM).
DRIFT_TEMP_K = float(os.getenv("DRIFT_TEMP_K", "8"))
DRIFT_SPEED_RPM = float(os.getenv("DRIFT_SPEED_RPM", "300"))
MAX_DELAY = 200  # readings; slower than this counts as "not detected"


def readings(df: pd.DataFrame, drift: bool = False) -> list[dict]:
    """Rows in time order as the signals the edge app reads (drift: shifted like drift mode)."""
    t, s = (DRIFT_TEMP_K, DRIFT_SPEED_RPM) if drift else (0.0, 0.0)
    df = df.sort_values("UDI")
    return [{"AirTempK": r.AirTempK + t, "ProcessTempK": r.ProcessTempK + t, "SpeedRpm": r.SpeedRpm + s,
             "TorqueNm": r.TorqueNm, "ToolWearMin": r.ToolWearMin} for r in df.itertuples()]


def false_rate(stats, rows, on, off) -> float:
    mon = DriftMonitor(stats, on, off)
    results = [mon.update(r) for r in rows]
    scored = [r["warning"] for r in results if r["score"] is not None]
    return float(np.mean(scored))


def delays(stats, normal, shifted, on, off, step=50) -> list[int]:
    """Delay in readings for drift starting at every `step`-th reading (after a normal warm-up)."""
    out = []
    for start in range(DRIFT_WINDOW, len(normal) - MAX_DELAY, step):
        mon = DriftMonitor(stats, on, off)
        for r in normal[start - DRIFT_WINDOW:start]:  # a full window of normal readings first
            mon.update(r)
        d = next((i for i, r in enumerate(shifted[start:start + MAX_DELAY], 1) if mon.update(r)["warning"]),
                 MAX_DELAY + 1)
        out.append(d)
    return out


def summary(stats, df, on, off) -> dict:
    normal, shifted = readings(df), readings(df, drift=True)
    d = np.array(delays(stats, normal, shifted, on, off))
    return {"false_warning_rate": round(false_rate(stats, normal, on, off), 4),
            "delay_median": float(np.median(d)), "delay_p95": float(np.percentile(d, 95)),
            "detected": float(np.mean(d <= MAX_DELAY))}


def main():
    info = c.read_info()
    stats = info["training_stats"]
    # UDI 1-8000: the whole past. The test period (UDI 8001-10000) is never used for the choice.
    calib = pd.concat([pd.read_csv(os.path.join(c.DATA_DIR, f"{n}.csv")) for n in ("train", "val")])
    test = pd.read_csv(os.path.join(c.DATA_DIR, "test.csv"))
    calib_rows = readings(calib)

    on = None
    for cand in np.round(np.arange(0.5, 5.01, 0.05), 2):
        if false_rate(stats, calib_rows, cand, round(OFF_RATIO * cand, 2)) <= TARGET_FALSE_RATE:
            on = float(cand)
            break
    if on is None:
        raise SystemExit("no threshold up to 5.0 meets the target on train + validation")
    off = round(OFF_RATIO * on, 2)

    result = {"on": on, "off": off, "window": DRIFT_WINDOW, "chosen_on": "train + validation (UDI 1-8000, time order)",
              "target_false_rate": TARGET_FALSE_RATE,
              "calibration": summary(stats, calib, on, off), "test": summary(stats, test, on, off)}
    print(f"drift thresholds chosen on train + validation: on={on} off={off} (target false warnings <= {TARGET_FALSE_RATE:.0%})")
    for part in ("calibration", "test"):
        r = result[part]
        print(f"  {part:11s}: false warnings {r['false_warning_rate']:.2%}, drift-mode delay median "
              f"{r['delay_median']:.0f} / p95 {r['delay_p95']:.0f} readings, detected {r['detected']:.0%}")
    c.write_info({"drift": result})


if __name__ == "__main__":
    main()
