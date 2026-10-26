"""Load the two ONNX models + model_info.json and turn one PLC reading into a prediction."""
import json
import os

import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.getenv("MODELS_DIR", os.path.join(ROOT, "models"))


class Models:
    def __init__(self, models_dir: str = MODELS_DIR):
        with open(os.path.join(models_dir, "model_info.json")) as fh:
            self.info = json.load(fh)
        self.features = self.info["features"]                 # model input order
        self.product_code = self.info["product_type_code"]    # "L"/"M"/"H" -> 0/1/2
        self.threshold = float(self.info["threshold"])
        self.version = self.info["model_version"]
        self.type_names = [self.info["failure_type_names"][t] for t in self.info["failure_types"]]
        self.medians = np.array([self.info["training_stats"][f]["median"] for f in self.features], dtype=np.float32)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # tiny models: one thread is fastest and leaves CPU for the rest
        self.risk_sess = ort.InferenceSession(os.path.join(models_dir, "risk.onnx"), opts,
                                              providers=["CPUExecutionProvider"])
        self.type_sess = ort.InferenceSession(os.path.join(models_dir, "failure_type.onnx"), opts,
                                              providers=["CPUExecutionProvider"])
        self.input_name = self.risk_sess.get_inputs()[0].name  # same name in both models

    def to_vector(self, signals: dict) -> np.ndarray:
        """PLC signals -> model input row [1, n_features], float32, in training order."""
        values = [self.product_code[signals[f]] if f == "ProductType" else signals[f] for f in self.features]
        return np.array([values], dtype=np.float32)

    def risk_batch(self, x: np.ndarray) -> np.ndarray:
        """Failure probability for each row of x (used by predict and by the explanation)."""
        return self.risk_sess.run(["probabilities"], {self.input_name: x})[0][:, 1]

    def predict(self, signals: dict) -> dict:
        x = self.to_vector(signals)
        risk = float(self.risk_batch(x)[0])
        type_probs = self.type_sess.run(["probabilities"], {self.input_name: x})[0][0]
        best = int(type_probs.argmax())
        return {
            "x": x,
            "risk": risk,
            "alarm": risk >= self.threshold,
            # Type model answers "IF it fails, which type?" -> only meaningful when risk is raised.
            "failure_type": self.type_names[best],
            "type_confidence": float(type_probs[best]),
        }
