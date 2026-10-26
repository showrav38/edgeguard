"""Milestone 1 check: models exist, work through ONNX, and meet basic quality bars.

Metrics are recomputed here from the ONNX files and data/test.csv (not copied from
model_info.json), so this also proves the exported models are the ones that perform well.
The bars are sanity limits, not targets. Prints PASS or FAIL and exits with code 0 / 1.
"""
import json
import os
import sys
import time

import numpy as np
import onnxruntime as ort
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "training"))
import common as c  # noqa: E402

MIN_ROC_AUC = 0.90
MIN_RECALL = 0.75          # share of test failures that raise an alarm at the chosen threshold
MIN_TYPE_ACCURACY = 0.80
MAX_P95_MS = 10.0
STAT_KEYS = ["mean", "std", "min", "max"]


def proba(sess, x):
    return sess.run(["probabilities"], {sess.get_inputs()[0].name: x})[0]


def main() -> int:
    errors = []

    def check(ok: bool, msg: str):
        print(("  ok   " if ok else "  FAIL ") + msg)
        if not ok:
            errors.append(msg)

    paths = ["models/risk.onnx", "models/failure_type.onnx", "models/model_info.json",
             "docs/cost_curve.png", "docs/rules_check.png"]
    missing = [p for p in paths if not os.path.isfile(os.path.join(ROOT, p))]
    check(not missing, f"output files exist {missing or ''}")
    if missing:
        print("M1 check: FAIL")
        return 1

    info = json.load(open(os.path.join(ROOT, "models/model_info.json")))
    test = pd.read_csv(os.path.join(c.DATA_DIR, "test.csv"))
    x = c.X(test)
    risk = ort.InferenceSession(os.path.join(ROOT, "models/risk.onnx"), providers=["CPUExecutionProvider"])
    ftype = ort.InferenceSession(os.path.join(ROOT, "models/failure_type.onnx"), providers=["CPUExecutionProvider"])

    # 1. Training stats for drift detection: every feature, every stat.
    stats = info.get("training_stats", {})
    ok = all(f in stats and all(k in stats[f] for k in STAT_KEYS) for f in c.FEATURES)
    check(ok, f"training stats (mean/std/min/max) for all {len(c.FEATURES)} features")

    # 2. ONNX equals sklearn (measured at export time).
    for name, oc in info["onnx_check"].items():
        if name == "max_allowed_diff":
            continue
        ok = oc["max_abs_diff"] <= info["onnx_check"]["max_allowed_diff"] and oc["same_decision"] == oc["rows"]
        check(ok, f"{name}.onnx matches sklearn: max diff {oc['max_abs_diff']:.1e}, "
                  f"same decision {oc['same_decision']}/{oc['rows']}")

    # 3. Risk model quality on the test set, via ONNX.
    y = test["Machine failure"].to_numpy()
    p = proba(risk, x)[:, 1]
    auc = roc_auc_score(y, p)
    t = info["threshold"]
    recall = float((p[y == 1] >= t).mean())
    check(auc >= MIN_ROC_AUC, f"risk ROC-AUC {auc:.3f} >= {MIN_ROC_AUC}")
    check(recall >= MIN_RECALL, f"risk recall at threshold {t:.2f}: {recall:.3f} >= {MIN_RECALL}")

    # 4. Cost-based threshold is no worse than 0.5 on validation (where it was chosen).
    tv = info["threshold_validation"]
    check(tv["chosen"]["cost"] <= tv["default"]["cost"],
          f"validation cost at {t:.2f} = {tv['chosen']['cost']:.0f} <= cost at 0.50 = {tv['default']['cost']:.0f}")

    # 5. Failure-type model on test failure rows, via ONNX.
    t_test = c.type_rows(test)
    acc = float((proba(ftype, c.X(t_test)).argmax(1) == c.type_y(t_test)).mean())
    check(acc >= MIN_TYPE_ACCURACY, f"failure-type accuracy {acc:.3f} >= {MIN_TYPE_ACCURACY} (n={len(t_test)})")

    # 6. Latency: both models, one row at a time, 1000 rows.
    times = []
    for r in x[:1000]:
        t0 = time.perf_counter()
        proba(risk, r[None, :])
        proba(ftype, r[None, :])
        times.append((time.perf_counter() - t0) * 1000)
    p95 = float(np.percentile(times, 95))
    check(p95 <= MAX_P95_MS, f"latency mean {np.mean(times):.3f} ms, p95 {p95:.3f} ms <= {MAX_P95_MS} ms")

    print("M1 check:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
