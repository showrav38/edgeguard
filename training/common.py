"""Shared helpers for the training scripts: paths, feature names, loading and splitting the data.

Feature names match the OPC UA variable names of the simulated PLC, so the edge app can build
the model input directly from what it reads.
"""
import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MODELS_DIR = os.path.join(ROOT, "models")
DOCS_DIR = os.path.join(ROOT, "docs")
RAW_CSV = os.path.join(DATA_DIR, "ai4i2020.csv")
MODEL_INFO = os.path.join(MODELS_DIR, "model_info.json")

SEED = 42  # model random state (the split itself is deterministic, by time)

# Dataset column -> our feature name (= OPC UA variable name). Order = model input order.
COLUMN_MAP = {
    "Air temperature [K]": "AirTempK",
    "Process temperature [K]": "ProcessTempK",
    "Rotational speed [rpm]": "SpeedRpm",
    "Torque [Nm]": "TorqueNm",
    "Tool wear [min]": "ToolWearMin",
    "Type": "ProductType",
}
FEATURES = list(COLUMN_MAP.values())

# Product quality L < M < H, encoded as an ordered number.
PRODUCT_TYPE_CODE = {"L": 0, "M": 1, "H": 2}

# Failure types the type model predicts. List order = class index AND the priority used when a
# row has more than one type (a convention, see docs/decisions.md). RNF is not predictable.
FAILURE_TYPES = ["TWF", "HDF", "PWF", "OSF"]
FAILURE_TYPE_NAMES = {
    "TWF": "tool_wear",
    "HDF": "heat_dissipation",
    "PWF": "power",
    "OSF": "overstrain",
}


def load_raw() -> pd.DataFrame:
    """Read the CSV (it starts with a BOM, hence utf-8-sig) and add our feature columns."""
    df = pd.read_csv(RAW_CSV, encoding="utf-8-sig")
    df = df.rename(columns=COLUMN_MAP)
    df["ProductType"] = df["ProductType"].map(PRODUCT_TYPE_CODE)
    df["failure_type"] = df.apply(single_failure_type, axis=1)
    return df


def single_failure_type(row) -> str:
    """One label per row: the first failure type set, in FAILURE_TYPES priority order.

    Returns "" for rows with no predictable type (no failure, RNF only, or failure without a type).
    """
    for t in FAILURE_TYPES:
        if row[t] == 1:
            return t
    return ""


def split(df: pd.DataFrame):
    """Time-based split by UDI (the dataset is in time order: tool wear rises and resets).

    First 80 % = the past, used for development: UDI 1-6000 fit the models, UDI 6001-8000 choose
    the alarm threshold. Last 20 % (UDI 8001-10000) = the "future", used only for the test and
    replayed by the PLC simulator. No row from the future is used for training.
    """
    df = df.sort_values("UDI")
    n = len(df)
    train, val, test = df.iloc[: int(n * 0.6)], df.iloc[int(n * 0.6): int(n * 0.8)], df.iloc[int(n * 0.8):]
    return train, val, test


def X(df: pd.DataFrame) -> np.ndarray:
    """Model input matrix in the fixed feature order, float32 (what ONNX expects)."""
    return df[FEATURES].to_numpy(dtype=np.float32)


def type_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows used by the failure-type model: failures with a predictable type.

    isin() rather than != "": after a CSV round-trip the empty label is read back as NaN.
    """
    return df[df["failure_type"].isin(FAILURE_TYPES)]


def type_y(df: pd.DataFrame) -> np.ndarray:
    return df["failure_type"].map(FAILURE_TYPES.index).to_numpy()


def read_info() -> dict:
    if os.path.exists(MODEL_INFO):
        with open(MODEL_INFO) as fh:
            return json.load(fh)
    return {}


def write_info(updates: dict) -> None:
    """Merge `updates` into models/model_info.json (each script adds its own section)."""
    info = read_info()
    info.update(updates)
    os.makedirs(MODELS_DIR, exist_ok=True)
    with open(MODEL_INFO, "w") as fh:
        json.dump(info, fh, indent=2)
