"""Simulated PLC: an OPC UA server that replays test rows as live machine signals.

Namespace "urn:edgeguard:plc", object "Machine1".
  PLC writes : AirTempK, ProcessTempK, SpeedRpm, TorqueNm, ToolWearMin, ProductType, Seq,
               GroundTruthFailure, GroundTruthType, DriftMode (writable by clients, for the demo)
  Edge writes: FailureRisk, Alarm, FailureType, ActionCode, DriftWarning, InferenceMs,
               LastSeqProcessed, ModelVersion

One row per cycle. All signals are written first and Seq LAST, so a client that sees a new Seq
can read one consistent row. Seq keeps counting when the replay wraps around.

CycleMs (writable, demo) changes the replay speed while running (clamped to 20..2000 ms).

DriftMode = True shifts the signals outside the training range (hotter shop floor, faster speed)
to test drift detection (M4). Ground truth is left unchanged: we do not know the real outcome.

Run: python -m plc_sim.server      Config via environment variables (see below).
"""
import asyncio
import csv
import logging
import os
import statistics
import time

from asyncua import Server, ua
from asyncua.common.callback import CallbackType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENDPOINT = os.getenv("OPCUA_ENDPOINT", "opc.tcp://0.0.0.0:4840/edgeguard/")
NAMESPACE = "urn:edgeguard:plc"
CYCLE_MS = int(os.getenv("PLC_CYCLE_MS", "1000"))  # one reading per second, like a slow machine log
DATA_CSV = os.getenv("PLC_DATA", os.path.join(ROOT, "data", "test.csv"))
# Optional CSV file "seq,latency_ms": time from the PLC writing Seq to the edge app writing
# LastSeqProcessed for that Seq. Both timestamps are taken here, in one process, on one clock.
LATENCY_LOG = os.getenv("PLC_LATENCY_LOG", "")

# Drift: +8 K on both temperatures (training range is ~295-304 K air) and +300 rpm.
DRIFT_TEMP_K = float(os.getenv("DRIFT_TEMP_K", "8"))
DRIFT_SPEED_RPM = float(os.getenv("DRIFT_SPEED_RPM", "300"))

PRODUCT_TYPE = {"0": "L", "1": "M", "2": "H"}  # test.csv stores the model's code
FAILURE_TYPES = ["TWF", "HDF", "PWF", "OSF"]

# name -> (OPC UA type, initial value). Order of PLC_SIGNALS = order they are written.
PLC_SIGNALS = {
    "AirTempK": (ua.VariantType.Double, 0.0),
    "ProcessTempK": (ua.VariantType.Double, 0.0),
    "SpeedRpm": (ua.VariantType.Double, 0.0),
    "TorqueNm": (ua.VariantType.Double, 0.0),
    "ToolWearMin": (ua.VariantType.Double, 0.0),
    "ProductType": (ua.VariantType.String, ""),
    "GroundTruthFailure": (ua.VariantType.Boolean, False),
    "GroundTruthType": (ua.VariantType.String, "none"),
}
EDGE_OUTPUTS = {
    "FailureRisk": (ua.VariantType.Double, 0.0),
    "Alarm": (ua.VariantType.Boolean, False),
    "FailureType": (ua.VariantType.String, ""),
    "ActionCode": (ua.VariantType.Int16, 0),
    "DriftWarning": (ua.VariantType.Boolean, False),
    "InferenceMs": (ua.VariantType.Double, 0.0),
    "LastSeqProcessed": (ua.VariantType.Int32, 0),
    "ModelVersion": (ua.VariantType.String, ""),
}

log = logging.getLogger("plc_sim")


def load_rows(path: str) -> list[dict]:
    """Test rows in time (UDI) order: tool wear rises and resets like on a real machine."""
    with open(path, newline="") as fh:
        return sorted(csv.DictReader(fh), key=lambda r: int(r["UDI"]))


def ground_truth_type(row: dict) -> str:
    """Same priority as training: first of TWF > HDF > PWF > OSF, else RNF / unknown / none."""
    for t in FAILURE_TYPES:
        if row[t] == "1":
            return t
    if row["RNF"] == "1":
        return "RNF"
    return "unknown" if row["Machine failure"] == "1" else "none"


def row_values(row: dict, drift: bool) -> dict:
    """Turn one CSV row into the PLC signal values for one cycle."""
    temp_shift = DRIFT_TEMP_K if drift else 0.0
    speed_shift = DRIFT_SPEED_RPM if drift else 0.0
    return {
        "AirTempK": float(row["AirTempK"]) + temp_shift,
        "ProcessTempK": float(row["ProcessTempK"]) + temp_shift,
        "SpeedRpm": float(row["SpeedRpm"]) + speed_shift,
        "TorqueNm": float(row["TorqueNm"]),
        "ToolWearMin": float(row["ToolWearMin"]),
        "ProductType": PRODUCT_TYPE[row["ProductType"]],
        "GroundTruthFailure": row["Machine failure"] == "1",
        "GroundTruthType": ground_truth_type(row),
    }


async def add_vars(ns: int, parent, spec: dict, writable: bool) -> dict:
    nodes = {}
    for name, (vtype, initial) in spec.items():
        # String node ids like "ns=2;s=Machine1.AirTempK" are readable in any OPC UA browser.
        # Browse names need our namespace too: a plain string would put them in namespace 0.
        node = await parent.add_variable(
            ua.NodeId(f"Machine1.{name}", ns), ua.QualifiedName(name, ns), ua.Variant(initial, vtype))
        if writable:
            await node.set_writable()
        nodes[name] = node
    return nodes


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)  # asyncua is very chatty at INFO

    rows = load_rows(DATA_CSV)
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("EdgeGuard simulated PLC")
    # Demo only: no encryption / login. A real plant would use certificates (Basic256Sha256).
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    ns = await server.register_namespace(NAMESPACE)
    machine = await server.nodes.objects.add_object(ua.NodeId("Machine1", ns), ua.QualifiedName("Machine1", ns))
    plc = await add_vars(ns, machine, PLC_SIGNALS, writable=False)
    await add_vars(ns, machine, EDGE_OUTPUTS, writable=True)
    seq_node = await machine.add_variable(
        ua.NodeId("Machine1.Seq", ns), ua.QualifiedName("Seq", ns), ua.Variant(0, ua.VariantType.Int32))
    drift_node = await machine.add_variable(
        ua.NodeId("Machine1.DriftMode", ns), ua.QualifiedName("DriftMode", ns),
        ua.Variant(False, ua.VariantType.Boolean))
    await drift_node.set_writable()
    cycle_node = await machine.add_variable(
        ua.NodeId("Machine1.CycleMs", ns), ua.QualifiedName("CycleMs", ns),
        ua.Variant(CYCLE_MS, ua.VariantType.Int32))
    await cycle_node.set_writable()

    # --- End-to-end latency: Seq written (here) -> LastSeqProcessed written (by the edge app) ---
    last_seq_id = ua.NodeId("Machine1.LastSeqProcessed", ns)
    seq_written_at: dict[int, float] = {}  # seq -> perf_counter() when Seq was written
    recent_ms: list[float] = []
    latency_file = open(LATENCY_LOG, "w") if LATENCY_LOG else None
    if latency_file:
        latency_file.write("seq,latency_ms\n")

    def on_write(event, _dispatcher):
        """asyncua calls this after every OPC UA write request (public PostWrite callback)."""
        for wv in event.request_params.NodesToWrite:
            if wv.NodeId != last_seq_id:
                continue
            t_seq = seq_written_at.pop(wv.Value.Value.Value, None)
            if t_seq is None:
                continue
            ms = (time.perf_counter() - t_seq) * 1000
            recent_ms.append(ms)
            if latency_file:
                latency_file.write(f"{wv.Value.Value.Value},{ms:.3f}\n")
                latency_file.flush()

    server.subscribe_server_callback(CallbackType.PostWrite, on_write)

    log.info("replaying %d rows from %s every %d ms on %s", len(rows), DATA_CSV, CYCLE_MS, ENDPOINT)
    async with server:
        seq = 0
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            row = rows[seq % len(rows)]
            drift = await drift_node.read_value()
            for name, value in row_values(row, drift).items():
                await plc[name].write_value(ua.Variant(value, PLC_SIGNALS[name][0]))
            seq += 1
            seq_written_at[seq] = time.perf_counter()
            seq_written_at.pop(seq - 100, None)  # forget rows the edge app never answered
            await seq_node.write_value(ua.Variant(seq, ua.VariantType.Int32))  # last: row complete
            if seq % 100 == 0:
                if recent_ms:
                    log.info("seq=%d drift=%s e2e latency (last %d): mean %.1f ms, max %.1f ms",
                             seq, drift, len(recent_ms), statistics.mean(recent_ms), max(recent_ms))
                else:
                    log.info("seq=%d drift=%s (no edge app answering)", seq, drift)
                recent_ms.clear()

            # Fixed-rate schedule (no slow drift of the cycle time over hours).
            cycle_ms = min(2000, max(20, await cycle_node.read_value()))
            next_tick += cycle_ms / 1000
            await asyncio.sleep(max(0.0, next_tick - loop.time()))


if __name__ == "__main__":
    asyncio.run(main())
