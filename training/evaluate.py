"""Evaluate both models on the held-out TEST set (never used for training or threshold choice).

Prints precision / recall / F1 / ROC-AUC and confusion matrices, at the cost-based threshold and
at the default 0.5. Writes the numbers to models/model_info.json -> "test_metrics".
"""
import os

import joblib
import pandas as pd
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
    precision_score,
    recall_score,
    roc_auc_score,
)

import common as c


def binary_metrics(y, p, t) -> dict:
    pred = (p >= t).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "threshold": t,
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred)),
        "f1": float(f1_score(y, pred)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def main():
    info = c.read_info()
    threshold = info.get("threshold", 0.5)
    test = pd.read_csv(os.path.join(c.DATA_DIR, "test.csv"))

    # ---------------- Risk model ----------------
    risk = joblib.load(os.path.join(c.MODELS_DIR, "risk.joblib"))
    y = test["Machine failure"].to_numpy()
    p = risk.predict_proba(c.X(test))[:, 1]
    auc = float(roc_auc_score(y, p))
    chosen, default = binary_metrics(y, p, threshold), binary_metrics(y, p, 0.5)

    print(f"=== Risk model, test set: {len(y)} rows, {int(y.sum())} failures ===")
    print(f"ROC-AUC: {auc:.3f}")
    for name, m in [("cost-based", chosen), ("default", default)]:
        cm = m["confusion"]
        print(f"  {name:10s} t={m['threshold']:.2f}: precision={m['precision']:.3f} recall={m['recall']:.3f} "
              f"f1={m['f1']:.3f} | TP={cm['tp']} FN={cm['fn']} FP={cm['fp']} TN={cm['tn']}")

    # Recall per failure kind: which failures does the alarm catch? RNF is expected to be ~0.
    pred = p >= threshold
    recall_by_kind = {}
    print(f"  recall by failure kind at t={threshold:.2f} (small counts -> noisy):")
    for kind in c.FAILURE_TYPES + ["RNF"]:
        mask = (test[kind] == 1).to_numpy()
        n = int(mask.sum())
        r = float(pred[mask].mean()) if n else float("nan")
        recall_by_kind[kind] = {"n": n, "recall": r}
        print(f"    {kind}: {r:.2f}  (n={n})")

    # ---------------- Failure-type model ----------------
    ftype = joblib.load(os.path.join(c.MODELS_DIR, "failure_type.joblib"))
    t_test = c.type_rows(test)
    yt = c.type_y(t_test)
    pt = ftype.predict(c.X(t_test))
    labels = list(range(len(c.FAILURE_TYPES)))
    prec, rec, f1, support = precision_recall_fscore_support(yt, pt, labels=labels, zero_division=0)
    cm = confusion_matrix(yt, pt, labels=labels)
    accuracy = float((yt == pt).mean())

    print(f"\n=== Failure-type model, test failure rows with a type: {len(yt)} ===")
    print(f"accuracy: {accuracy:.3f}   (counts are small -> per-type numbers are noisy)")
    per_type = {}
    for i, t in enumerate(c.FAILURE_TYPES):
        per_type[t] = {"n": int(support[i]), "precision": float(prec[i]), "recall": float(rec[i]), "f1": float(f1[i])}
        print(f"  {t}: precision={prec[i]:.2f} recall={rec[i]:.2f} f1={f1[i]:.2f}  (n={support[i]})")
    print("  confusion matrix (rows = true, cols = predicted):", " ".join(c.FAILURE_TYPES))
    for i, t in enumerate(c.FAILURE_TYPES):
        print(f"    {t}: {cm[i].tolist()}")

    c.write_info({
        "test_metrics": {
            "risk": {"roc_auc": auc, "cost_based": chosen, "default_0_5": default,
                     "recall_by_failure_kind": recall_by_kind},
            "failure_type": {"n": int(len(yt)), "accuracy": accuracy, "per_type": per_type,
                             "confusion": cm.tolist()},
        }
    })


if __name__ == "__main__":
    main()
