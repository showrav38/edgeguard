"""Drift detection: does the live data still look like the training data?

Method (rolling-window mean shift, a z-score):
  for each monitored signal, over the last DRIFT_WINDOW readings:
      score = |mean(window) - training mean| / training std
  drift_score = the largest score over the signals.
  Warning ON when drift_score >= DRIFT_ON, OFF again only when it falls below DRIFT_OFF. The gap
  (hysteresis) stops the warning from flickering at the border.

Thresholds are chosen on training + validation (UDI 1-8000, both seasons) by
training/calibrate_drift.py (target: at most 1 % false warnings on normal data) and stored in models/model_info.json -> "drift". The env vars
DRIFT_ON / DRIFT_OFF override them. See docs/decisions.md D-21.

ToolWearMin is NOT monitored by default: it rises naturally until the tool is changed, and a
batch of worn tools is exactly what the model should alarm on, not a reason to distrust it.
ProductType is a category, a mean shift means little there.

Limits: looks at each signal alone (a new combination of normal values is not seen), and at the
mean only (a change in spread is not seen). PSI on the training deciles would catch shape changes
but is noisy with a 50-reading window.
"""
import os
from collections import deque

DRIFT_WINDOW = int(os.getenv("DRIFT_WINDOW", "50"))
DRIFT_SIGNALS = os.getenv("DRIFT_SIGNALS", "AirTempK,ProcessTempK,SpeedRpm,TorqueNm").split(",")


def thresholds(info: dict) -> tuple[float, float]:
    """(on, off): env vars if set, else the values calibrated on validation (model_info.json)."""
    return (float(os.getenv("DRIFT_ON") or info["drift"]["on"]),
            float(os.getenv("DRIFT_OFF") or info["drift"]["off"]))


class DriftMonitor:
    def __init__(self, training_stats: dict, on: float, off: float,
                 signals: list[str] = DRIFT_SIGNALS, window: int = DRIFT_WINDOW):
        self.stats = {s: training_stats[s] for s in signals}
        self.window = window
        self.on, self.off = on, off
        self.values = {s: deque(maxlen=window) for s in signals}  # last N readings per signal
        self.warning = False

    def update(self, signals: dict) -> dict:
        """Add one reading. Returns {score, warning, signals: the signals above the on threshold}."""
        for s, buf in self.values.items():
            buf.append(float(signals[s]))

        n = len(next(iter(self.values.values())))
        if n < self.window:
            # Not enough readings yet for a stable mean: no score, no warning.
            return {"score": None, "warning": False, "signals": []}

        scores = {}
        for s, buf in self.values.items():
            st = self.stats[s]
            scores[s] = abs(sum(buf) / n - st["mean"]) / st["std"]
        score = max(scores.values())

        if score >= self.on:
            self.warning = True
        elif score < self.off:
            self.warning = False
        drifted = sorted((s for s in scores if scores[s] >= self.on), key=lambda s: -scores[s])
        return {"score": round(score, 3), "warning": self.warning, "signals": drifted}
