"""Turn a prediction into a machine state and an action the technician can act on.

States:
  ALARM    risk >= cost-based threshold
  WARNING  risk >= WARNING_FRACTION x threshold  (risk is rising; M4 also uses it for drift)
  OK       otherwise

ActionCode (Int16 written to the PLC, so a PLC/HMI can react without parsing text):
  0 no action | 1 replace tool | 2 check cooling | 3 check power / drive | 4 reduce load
  5 watch (warning)
"""
import math
import os

WARNING_FRACTION = float(os.getenv("WARNING_FRACTION", "0.5"))

ACTION_CODE = {"tool_wear": 1, "heat_dissipation": 2, "power": 3, "overstrain": 4}
CODE_OK, CODE_WATCH = 0, 5


def machine_state(risk: float, threshold: float) -> str:
    if risk >= threshold:
        return "ALARM"
    if risk >= WARNING_FRACTION * threshold:
        return "WARNING"
    return "OK"


def alarm_text(failure_type: str, s: dict) -> str:
    """One sentence per failure type, filled with the live values that matter for it."""
    if failure_type == "tool_wear":
        return f"Tool wear {s['ToolWearMin']:.0f} min: replace the tool before the next batch."
    if failure_type == "heat_dissipation":
        diff = s["ProcessTempK"] - s["AirTempK"]
        return (f"Heat dissipation: only {diff:.1f} K between process and air at {s['SpeedRpm']:.0f} rpm. "
                "Check cooling and fans; avoid running at low speed.")
    if failure_type == "power":
        power_w = s["TorqueNm"] * s["SpeedRpm"] * 2 * math.pi / 60
        return f"Power {power_w / 1000:.1f} kW outside the normal range: check load and drive settings."
    # overstrain
    return (f"Overstrain: tool wear {s['ToolWearMin']:.0f} min at {s['TorqueNm']:.1f} Nm torque. "
            "Reduce the load or change the tool.")


def decide(pred: dict, signals: dict, threshold: float) -> dict:
    state = machine_state(pred["risk"], threshold)
    if state == "ALARM":
        ftype = pred["failure_type"]
        return {"state": state, "failure_type": ftype, "code": ACTION_CODE[ftype],
                "text": alarm_text(ftype, signals)}
    if state == "WARNING":
        ftype = pred["failure_type"]
        return {"state": state, "failure_type": ftype, "code": CODE_WATCH,
                "text": f"Risk rising ({pred['risk']:.0%}, most likely {ftype.replace('_', ' ')}): "
                        "watch the machine and plan a check at the next stop."}
    return {"state": state, "failure_type": "none", "code": CODE_OK, "text": "Machine OK: no action needed."}
