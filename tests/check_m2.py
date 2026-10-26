"""Milestone 2 check: the simulated PLC serves the agreed OPC UA interface and replays the data.

Starts its own PLC simulator (port 4841, 100 ms cycle) as a subprocess, connects as a client:
  1. namespace + Machine1 + all 18 variables with the right OPC UA types
  2. subscribe to Seq: N cycles in a row, no gaps, steady cycle time
  3. each row read after a Seq change is consistent (Seq unchanged) and equals data/test.csv
  4. edge outputs are writable, PLC signals are read-only
  5. DriftMode shifts the signals; turning it off restores them
Prints PASS or FAIL and exits with code 0 / 1.
"""
import asyncio
import csv
import os
import subprocess
import sys
import time

from asyncua import Client, ua

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from plc_sim import server as plc  # noqa: E402  (reuse the interface definition and helpers)

PORT = 4841
CYCLE_MS = 100
N_CYCLES = 50
URL = f"opc.tcp://127.0.0.1:{PORT}/edgeguard/"
SIGNALS = ["AirTempK", "ProcessTempK", "SpeedRpm", "TorqueNm", "ToolWearMin", "ProductType",
           "GroundTruthFailure", "GroundTruthType"]

errors = []


def check(ok: bool, msg: str):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        errors.append(msg)


class SeqHandler:
    """asyncua calls datachange_notification for every Seq change; we queue them with a timestamp."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    def datachange_notification(self, node, val, data):
        self.queue.put_nowait((val, time.perf_counter()))


async def connect() -> Client:
    for _ in range(50):  # the server needs a moment to start
        client = Client(URL)
        try:
            await client.connect()
            return client
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.3)
    raise RuntimeError(f"could not connect to {URL}")


async def run_checks():
    rows = plc.load_rows(plc.DATA_CSV)
    client = await connect()
    try:
        # 1. Interface: namespace, object, variables, types.
        ns = await client.get_namespace_index(plc.NAMESPACE)
        machine = await client.nodes.objects.get_child([f"{ns}:Machine1"])
        names = {(await ch.read_browse_name()).Name for ch in await machine.get_children()}
        expected = {**plc.PLC_SIGNALS, **plc.EDGE_OUTPUTS,
                    "Seq": (ua.VariantType.Int32, 0), "DriftMode": (ua.VariantType.Boolean, False)}
        missing = sorted(set(expected) - names)
        check(not missing, f"Machine1 in '{plc.NAMESPACE}' has all {len(expected)} variables {missing or ''}")
        node = {n: client.get_node(ua.NodeId(f"Machine1.{n}", ns)) for n in expected}
        wrong = [n for n, (vt, _) in expected.items()
                 if (await node[n].read_data_value()).Value.VariantType != vt]
        check(not wrong, f"variable types match the interface {wrong or ''}")

        # 2 + 3. Subscribe to Seq, read the full row after each change.
        handler = SeqHandler()
        sub = await client.create_subscription(CYCLE_MS / 4, handler)
        await sub.subscribe_data_change(node["Seq"], queuesize=10)
        await handler.queue.get()  # first notification = current value, skip it
        row_nodes = [node["Seq"]] + [node[s] for s in SIGNALS]
        seqs, stamps, inconsistent, mismatched = [], [], 0, 0
        while len(seqs) < N_CYCLES:
            seq, t = await asyncio.wait_for(handler.queue.get(), timeout=2)
            values = await client.read_values(row_nodes)  # ONE read request for the whole row
            if values[0] != seq:
                inconsistent += 1
            want = plc.row_values(rows[(seq - 1) % len(rows)], drift=False)
            if any(abs(v - want[s]) > 1e-9 if isinstance(v, float) else v != want[s]
                   for s, v in zip(SIGNALS, values[1:])):
                mismatched += 1
            seqs.append(seq)
            stamps.append(t)
        await sub.delete()

        gaps = sum(b - a != 1 for a, b in zip(seqs, seqs[1:]))
        check(gaps == 0, f"{N_CYCLES} cycles received, Seq {seqs[0]}..{seqs[-1]}, gaps: {gaps}")
        intervals = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:])]
        mean_ms = sum(intervals) / len(intervals)
        check(abs(mean_ms - CYCLE_MS) < 0.2 * CYCLE_MS, f"mean cycle {mean_ms:.1f} ms (target {CYCLE_MS} ms: the check runs the PLC "
                                                              f"faster than the 200 ms default)")
        check(inconsistent == 0, f"rows consistent (Seq unchanged during read): {N_CYCLES - inconsistent}/{N_CYCLES}")
        check(mismatched == 0, f"rows equal data/test.csv: {N_CYCLES - mismatched}/{N_CYCLES}")

        # 4. Write access: edge outputs writable, PLC signals read-only.
        test_writes = {"FailureRisk": 0.42, "Alarm": True, "FailureType": "tool_wear", "ActionCode": 3,
                       "DriftWarning": True, "InferenceMs": 1.5, "LastSeqProcessed": 7, "ModelVersion": "check"}
        for n, v in test_writes.items():
            await node[n].write_value(ua.Variant(v, plc.EDGE_OUTPUTS[n][0]))
        read_back = {n: await node[n].read_value() for n in test_writes}
        check(read_back == test_writes, "edge outputs writable and read back equal")
        try:
            await node["AirTempK"].write_value(ua.Variant(999.0, ua.VariantType.Double))
            read_only = False
        except ua.UaStatusCodeError:
            read_only = True
        check(read_only, "PLC signal AirTempK rejects client writes")

        # 5. DriftMode on: signals shifted vs the CSV row. Off: back to the CSV values.
        async def shift_now() -> tuple[float, float]:
            await asyncio.sleep(3 * CYCLE_MS / 1000)  # let a few drifted cycles pass
            seq, air, speed = await client.read_values([node["Seq"], node["AirTempK"], node["SpeedRpm"]])
            row = rows[(seq - 1) % len(rows)]
            return air - float(row["AirTempK"]), speed - float(row["SpeedRpm"])

        await node["DriftMode"].write_value(ua.Variant(True, ua.VariantType.Boolean))
        d_on = await shift_now()
        await node["DriftMode"].write_value(ua.Variant(False, ua.VariantType.Boolean))
        d_off = await shift_now()
        check(abs(d_on[0] - plc.DRIFT_TEMP_K) < 1e-6 and abs(d_on[1] - plc.DRIFT_SPEED_RPM) < 1e-6,
              f"DriftMode on: air +{d_on[0]:.1f} K, speed +{d_on[1]:.0f} rpm")
        check(abs(d_off[0]) < 1e-6 and abs(d_off[1]) < 1e-6, "DriftMode off: signals back to the data")
    finally:
        await client.disconnect()


def main() -> int:
    env = {**os.environ, "OPCUA_ENDPOINT": f"opc.tcp://0.0.0.0:{PORT}/edgeguard/", "PLC_CYCLE_MS": str(CYCLE_MS)}
    proc = subprocess.Popen([sys.executable, "-m", "plc_sim.server"], cwd=ROOT, env=env)
    try:
        asyncio.run(run_checks())
    except Exception as ex:  # any crash (no connection, timeout) is a FAIL with the reason
        check(False, f"{type(ex).__name__}: {ex}")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    print("M2 check:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
