"""Train the two models and save the training statistics.

1. Risk model: will this reading end in a machine failure? (all rows, all failure kinds)
2. Failure-type model: if it fails, which type? (only failure rows with a predictable type)

Outputs:
  models/risk.joblib, models/failure_type.joblib   (sklearn models, not committed)
  data/train.csv, data/val.csv, data/test.csv       (the split; PLC simulator replays test.csv)
  models/model_info.json -> "features", "training_stats", "failure_types", "split"
"""
import os

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.utils.class_weight import compute_sample_weight

import common as c


def make_model() -> GradientBoostingClassifier:
    # Small, standard settings; trees handle the raw physical signals without scaling.
    return GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.1, random_state=c.SEED)


def training_stats(train) -> dict:
    """Per-feature stats used later for drift detection (M4) and explanations (M3)."""
    stats = {}
    for f in c.FEATURES:
        v = train[f].to_numpy(dtype=float)
        stats[f] = {
            "mean": float(v.mean()),
            "std": float(v.std()),
            "min": float(v.min()),
            "max": float(v.max()),
            "median": float(np.median(v)),
            # Decile edges: bins for a PSI drift score.
            "deciles": [float(q) for q in np.quantile(v, np.linspace(0.1, 0.9, 9))],
        }
    return stats


def main():
    df = c.load_raw()
    train, val, test = c.split(df)
    for name, part in [("train", train), ("val", val), ("test", test)]:
        part.to_csv(os.path.join(c.DATA_DIR, f"{name}.csv"), index=False)
        print(f"{name:5s}: {len(part):5d} rows, {int(part['Machine failure'].sum()):3d} failures")

    # --- Risk model: 'balanced' sample weights make each failure count ~29x a normal row,
    # so the model does not just predict "no failure" for the 3.4% minority.
    y_risk = train["Machine failure"].to_numpy()
    risk = make_model()
    risk.fit(c.X(train), y_risk, sample_weight=compute_sample_weight("balanced", y_risk))

    # --- Failure-type model: trained only on failure rows that have one of the 4 types.
    # RNF-only and no-type failures stay in the risk model but are dropped here.
    t_train = c.type_rows(train)
    y_type = c.type_y(t_train)
    ftype = make_model()
    ftype.fit(c.X(t_train), y_type, sample_weight=compute_sample_weight("balanced", y_type))
    counts = {t: int((y_type == i).sum()) for i, t in enumerate(c.FAILURE_TYPES)}
    print(f"type model trained on {len(t_train)} failure rows: {counts}")

    joblib.dump(risk, os.path.join(c.MODELS_DIR, "risk.joblib"))
    joblib.dump(ftype, os.path.join(c.MODELS_DIR, "failure_type.joblib"))

    c.write_info({
        "features": c.FEATURES,
        "product_type_code": c.PRODUCT_TYPE_CODE,
        "failure_types": c.FAILURE_TYPES,
        "failure_type_names": c.FAILURE_TYPE_NAMES,
        "training_stats": training_stats(train),
        "split": {"method": "time (UDI order)", "train_udi": [int(train.UDI.min()), int(train.UDI.max())],
                  "val_udi": [int(val.UDI.min()), int(val.UDI.max())],
                  "test_udi": [int(test.UDI.min()), int(test.UDI.max())],
                  "train": len(train), "val": len(val), "test": len(test)},
        # Mean decrease in impurity: a rough global ranking, reported for interest only.
        "risk_feature_importance": dict(zip(c.FEATURES, map(float, risk.feature_importances_))),
    })
    print("saved models/risk.joblib, models/failure_type.joblib, models/model_info.json")


if __name__ == "__main__":
    main()
