"""Unit tests for the edge app logic (no PLC or broker needed; uses the real ONNX models)."""
import numpy as np
import pytest

from edge_app import actions, explain
from edge_app.inference import Models

NORMAL = {"AirTempK": 300.0, "ProcessTempK": 310.0, "SpeedRpm": 1500.0, "TorqueNm": 40.0,
          "ToolWearMin": 100.0, "ProductType": "M"}


@pytest.fixture(scope="module")
def models():
    return Models()


def test_to_vector_uses_training_order_and_product_code(models):
    x = models.to_vector(NORMAL)
    assert x.shape == (1, len(models.features)) and x.dtype == np.float32
    assert x[0, models.features.index("ProductType")] == models.product_code["M"]
    assert x[0, models.features.index("ToolWearMin")] == 100.0


def test_normal_reading_is_ok(models):
    pred = models.predict(NORMAL)
    decision = actions.decide(pred, NORMAL, models.threshold)
    assert decision["state"] == "OK" and decision["code"] == actions.CODE_OK


def test_worn_tool_under_load_alarms_with_tool_action(models):
    # Overstrain region: tool wear x torque = 230 x 60 = 13,800 > 11,000 (L limit).
    s = {**NORMAL, "ToolWearMin": 230.0, "TorqueNm": 60.0, "ProductType": "L"}
    pred = models.predict(s)
    decision = actions.decide(pred, s, models.threshold)
    assert decision["state"] == "ALARM"
    assert decision["code"] in (actions.ACTION_CODE["overstrain"], actions.ACTION_CODE["tool_wear"])
    factors = [f["signal"] for f in explain.top_factors(models, pred["x"], pred["risk"])]
    assert "ToolWearMin" in factors or "TorqueNm" in factors


def test_top_factors_sorted_by_effect(models):
    s = {**NORMAL, "ToolWearMin": 230.0, "TorqueNm": 60.0}
    pred = models.predict(s)
    factors = explain.top_factors(models, pred["x"], pred["risk"], k=6)
    effects = [f["effect"] for f in factors]
    assert len(factors) == 6 and effects == sorted(effects, reverse=True)


def test_machine_state_bands():
    assert actions.machine_state(0.30, 0.22) == "ALARM"
    assert actions.machine_state(0.12, 0.22) == "WARNING"   # >= 0.5 x threshold
    assert actions.machine_state(0.05, 0.22) == "OK"
