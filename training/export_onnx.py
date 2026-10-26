"""Export both sklearn models to ONNX, verify the outputs match, and measure latency.

Outputs: models/risk.onnx, models/failure_type.onnx,
         models/model_info.json -> "onnx_check", "latency_ms", "versions", "model_version"
"""
import os
import platform
import time

import joblib
import numpy as np
import onnxruntime as ort
import pandas as pd
import skl2onnx
import sklearn
from skl2onnx import to_onnx

import common as c

MODEL_VERSION = os.getenv("MODEL_VERSION", "1.0.0")
MAX_ABS_DIFF = 1e-5  # allowed probability difference sklearn (float64) vs ONNX (float32)


def export(model, name: str, sample: np.ndarray) -> str:
    # zipmap=False -> probabilities come out as a plain [n, classes] float tensor, not a list of dicts.
    onx = to_onnx(model, sample[:1], options={id(model): {"zipmap": False}}, target_opset=17)
    path = os.path.join(c.MODELS_DIR, f"{name}.onnx")
    with open(path, "wb") as fh:
        fh.write(onx.SerializeToString())
    return path


def onnx_proba(sess: ort.InferenceSession, x: np.ndarray) -> np.ndarray:
    # Outputs are [label, probabilities]; we use the probabilities.
    return sess.run(["probabilities"], {sess.get_inputs()[0].name: x})[0]


def latency_ms(sessions, x: np.ndarray, n: int = 1000) -> dict:
    """Time one row at a time, as at the edge (one reading per cycle), through all sessions."""
    rows = x[:n]
    for r in rows[:50]:  # warm-up
        for s in sessions:
            onnx_proba(s, r[None, :])
    times = []
    for r in rows:
        t0 = time.perf_counter()
        for s in sessions:
            onnx_proba(s, r[None, :])
        times.append((time.perf_counter() - t0) * 1000)
    return {"n": len(times), "mean": float(np.mean(times)), "p95": float(np.percentile(times, 95))}


def main():
    test = pd.read_csv(os.path.join(c.DATA_DIR, "test.csv"))
    x = c.X(test)
    info = c.read_info()
    threshold = info.get("threshold", 0.5)

    check = {}
    sessions = []
    for name in ["risk", "failure_type"]:
        model = joblib.load(os.path.join(c.MODELS_DIR, f"{name}.joblib"))
        path = export(model, name, x)
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        sessions.append(sess)

        p_sk = model.predict_proba(x)
        p_onnx = onnx_proba(sess, x)
        diff = float(np.abs(p_sk - p_onnx).max())
        if name == "risk":  # same alarm decision for every row?
            same = int(((p_sk[:, 1] >= threshold) == (p_onnx[:, 1] >= threshold)).sum())
        else:  # same predicted type for every row?
            same = int((p_sk.argmax(1) == p_onnx.argmax(1)).sum())
        check[name] = {"max_abs_diff": diff, "same_decision": same, "rows": len(x)}
        print(f"{name:12s}: max |sklearn - onnx| = {diff:.2e}, same decision {same}/{len(x)}  -> {path}")

    lat = latency_ms(sessions, x)
    print(f"latency (risk + type, 1 row, {lat['n']} rows): mean={lat['mean']:.3f} ms  p95={lat['p95']:.3f} ms")

    c.write_info({
        "model_version": MODEL_VERSION,
        "onnx_check": {"max_allowed_diff": MAX_ABS_DIFF, **check},
        "latency_ms": lat,
        "versions": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "skl2onnx": skl2onnx.__version__,
            "onnxruntime": ort.__version__,
        },
    })


if __name__ == "__main__":
    main()
