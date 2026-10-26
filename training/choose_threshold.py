"""Pick the alarm threshold that minimises expected cost on the VALIDATION set.

cost(threshold) = COST_MISSED_FAILURE * false_negatives + COST_FALSE_ALARM * false_positives

A missed failure (unplanned stop, scrap, damage) costs far more than a false alarm (a technician
checks a healthy machine), so the best threshold is usually well below the default 0.5.

Outputs: docs/cost_curve.png, models/model_info.json -> "threshold"
"""
import os

import joblib
import matplotlib

matplotlib.use("Agg")  # no display needed (WSL / Docker)
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import common as c

COST_FALSE_ALARM = float(os.getenv("COST_FALSE_ALARM", "1"))
COST_MISSED_FAILURE = float(os.getenv("COST_MISSED_FAILURE", "20"))


def cost_at(y_true, p, t) -> tuple[float, int, int]:
    pred = p >= t
    fn = int(((y_true == 1) & ~pred).sum())
    fp = int(((y_true == 0) & pred).sum())
    return COST_MISSED_FAILURE * fn + COST_FALSE_ALARM * fp, fn, fp


def main():
    risk = joblib.load(os.path.join(c.MODELS_DIR, "risk.joblib"))
    val = pd.read_csv(os.path.join(c.DATA_DIR, "val.csv"))
    y = val["Machine failure"].to_numpy()
    p = risk.predict_proba(c.X(val))[:, 1]

    thresholds = np.round(np.arange(0.01, 1.0, 0.01), 2)
    costs = np.array([cost_at(y, p, t)[0] for t in thresholds])
    best = float(thresholds[costs.argmin()])  # first (lowest) threshold with the minimum cost

    best_cost, best_fn, best_fp = cost_at(y, p, best)
    def_cost, def_fn, def_fp = cost_at(y, p, 0.5)
    print(f"costs: missed failure = {COST_MISSED_FAILURE:g}, false alarm = {COST_FALSE_ALARM:g}")
    print(f"validation: {len(y)} rows, {int(y.sum())} failures")
    print(f"  chosen t={best:.2f}: cost={best_cost:6.0f}  missed={best_fn:3d}  false alarms={best_fp:3d}")
    print(f"  default t=0.50: cost={def_cost:6.0f}  missed={def_fn:3d}  false alarms={def_fp:3d}")

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(thresholds, costs, color="#1f5f8b")
    ax.axvline(best, color="#2e7d32", ls="--", label=f"chosen {best:.2f} (cost {best_cost:.0f})")
    ax.axvline(0.5, color="#c62828", ls=":", label=f"default 0.50 (cost {def_cost:.0f})")
    ax.set_xlabel("alarm threshold on failure risk")
    ax.set_ylabel("expected cost on validation set")
    ax.set_title(f"Cost vs threshold (missed failure = {COST_MISSED_FAILURE:g} x false alarm)")
    ax.legend()
    fig.tight_layout()
    os.makedirs(c.DOCS_DIR, exist_ok=True)
    fig.savefig(os.path.join(c.DOCS_DIR, "cost_curve.png"), dpi=120)
    print("saved docs/cost_curve.png")

    c.write_info({
        "threshold": best,
        "cost_false_alarm": COST_FALSE_ALARM,
        "cost_missed_failure": COST_MISSED_FAILURE,
        "cost_ratio": COST_MISSED_FAILURE / COST_FALSE_ALARM,
        "threshold_validation": {
            "chosen": {"threshold": best, "cost": best_cost, "missed": best_fn, "false_alarms": best_fp},
            "default": {"threshold": 0.5, "cost": def_cost, "missed": def_fn, "false_alarms": def_fp},
        },
    })


if __name__ == "__main__":
    main()
