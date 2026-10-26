"""Demo commands from the dashboard, received on the MQTT topic edgeguard/machine1/cmd.

  {"cmd": "drift",  "on": true}        DriftMode on the simulated PLC on / off
  {"cmd": "outage", "seconds": 20}     SIMULATED uplink outage inside the edge app (store-and-forward)
  {"cmd": "speed",  "cycle_ms": 100}   replay speed of the simulated PLC (one row every cycle_ms)

Every command is validated here before anything happens: unknown commands, wrong types and
out-of-range values are rejected (ValueError). The broker has no login in this demo, so anyone on
the network could send these; a real plant would disable them (DEMO_COMMANDS=0) or use ACLs.
"""
import json

MAX_OUTAGE_S = 120
MIN_CYCLE_MS, MAX_CYCLE_MS = 50, 1000


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)  # bool is an int in Python


def parse(payload: bytes) -> dict:
    """Return a clean command dict, or raise ValueError with a short reason."""
    try:
        msg = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("payload is not JSON") from None
    if not isinstance(msg, dict):
        raise ValueError("payload must be a JSON object")

    cmd = msg.get("cmd")
    if cmd == "drift":
        if not isinstance(msg.get("on"), bool):
            raise ValueError("drift: 'on' must be true or false")
        return {"cmd": "drift", "on": msg["on"]}
    if cmd == "outage":
        seconds = msg.get("seconds", 20)
        if not _is_number(seconds) or not 1 <= seconds <= MAX_OUTAGE_S:
            raise ValueError(f"outage: 'seconds' must be 1..{MAX_OUTAGE_S}")
        return {"cmd": "outage", "seconds": float(seconds)}
    if cmd == "speed":
        cycle_ms = msg.get("cycle_ms")
        if not _is_number(cycle_ms) or not MIN_CYCLE_MS <= cycle_ms <= MAX_CYCLE_MS:
            raise ValueError(f"speed: 'cycle_ms' must be {MIN_CYCLE_MS}..{MAX_CYCLE_MS}")
        return {"cmd": "speed", "cycle_ms": int(cycle_ms)}
    raise ValueError(f"unknown command {cmd!r}")
