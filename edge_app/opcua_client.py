"""OPC UA client for the PLC: wait for new rows, read them, write results back.

Protocol (see docs/decisions.md D-08): the PLC writes all signals and then Seq. We subscribe to
Seq only; on each change we read Seq + all signals in ONE read request. If Seq moved on between
the notification and the read, we simply process the newer row; skipped Seqs count as missed.
"""
import asyncio
import logging
import os

from asyncua import Client, ua

OPCUA_URL = os.getenv("OPCUA_URL", "opc.tcp://localhost:4840/edgeguard/")
NAMESPACE = "urn:edgeguard:plc"
PUBLISH_MS = float(os.getenv("OPCUA_PUBLISH_MS", "10"))  # how often the server sends us changes

SIGNALS = ["AirTempK", "ProcessTempK", "SpeedRpm", "TorqueNm", "ToolWearMin", "ProductType"]
EXTRA = ["GroundTruthFailure", "GroundTruthType", "DriftMode"]
OUTPUT_TYPES = {
    "FailureRisk": ua.VariantType.Double,
    "Alarm": ua.VariantType.Boolean,
    "FailureType": ua.VariantType.String,
    "ActionCode": ua.VariantType.Int16,
    "DriftWarning": ua.VariantType.Boolean,
    "InferenceMs": ua.VariantType.Double,
    "ModelVersion": ua.VariantType.String,
    "LastSeqProcessed": ua.VariantType.Int32,  # last on purpose: "this row is fully handled"
}

log = logging.getLogger("edge.opcua")


class _SeqHandler:
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    def datachange_notification(self, node, val, data):
        self.queue.put_nowait(val)


class PlcClient:
    def __init__(self, url: str = OPCUA_URL):
        self.url = url
        self.client = Client(url)

    async def connect(self, retries: int = 60):
        for attempt in range(retries):
            try:
                await self.client.connect()
                break
            except (OSError, asyncio.TimeoutError):
                log.info("PLC not reachable at %s (attempt %d), retrying", self.url, attempt + 1)
                await asyncio.sleep(1)
        else:
            raise ConnectionError(f"PLC not reachable at {self.url}")
        ns = await self.client.get_namespace_index(NAMESPACE)
        node = lambda name: self.client.get_node(ua.NodeId(f"Machine1.{name}", ns))  # noqa: E731
        self.seq_node = node("Seq")
        self.read_nodes = [self.seq_node] + [node(n) for n in SIGNALS + EXTRA]
        self.out_nodes = [node(n) for n in OUTPUT_TYPES]
        self.drift_node, self.cycle_node = node("DriftMode"), node("CycleMs")
        log.info("connected to PLC %s (namespace index %d)", self.url, ns)

    async def subscribe_seq(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        sub = await self.client.create_subscription(PUBLISH_MS, _SeqHandler(queue))
        # queuesize > 1: if two Seq changes fall into one publish interval, we still get both.
        await sub.subscribe_data_change(self.seq_node, queuesize=10)
        return queue

    async def read_row(self) -> tuple[int, dict, dict, bool]:
        values = await self.client.read_values(self.read_nodes)
        seq = values[0]
        signals = dict(zip(SIGNALS, values[1:1 + len(SIGNALS)]))
        gt_failure, gt_type, drift_mode = values[1 + len(SIGNALS):]
        return seq, signals, {"failure": gt_failure, "type": gt_type}, drift_mode

    async def write_outputs(self, outputs: dict):
        """Write all edge outputs in ONE write request (order: LastSeqProcessed last)."""
        variants = [ua.Variant(outputs[n], vt) for n, vt in OUTPUT_TYPES.items()]
        await self.client.write_values(self.out_nodes, variants)

    async def read_demo(self) -> dict:
        """Demo settings of the simulated PLC: drift mode and replay cycle time."""
        drift, cycle_ms = await self.client.read_values([self.drift_node, self.cycle_node])
        return {"drift_mode": drift, "cycle_ms": cycle_ms}

    async def write_demo(self, drift: bool | None = None, cycle_ms: int | None = None):
        """Demo commands from the dashboard (see commands.py)."""
        if drift is not None:
            await self.drift_node.write_value(ua.Variant(drift, ua.VariantType.Boolean))
        if cycle_ms is not None:
            await self.cycle_node.write_value(ua.Variant(cycle_ms, ua.VariantType.Int32))

    async def disconnect(self):
        await self.client.disconnect()
