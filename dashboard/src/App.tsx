// EdgeGuard technician dashboard: one machine, read top to bottom.
//   1. data-flow strip (is the data path healthy?)  2. drift banner
//   3. machine state + what to do now               4. live charts
//   5. alarm history + demo controls
import { AlarmHistory } from "./components/AlarmHistory";
import { ActionCard, DriftBanner, StatusCard } from "./components/Cards";
import { Charts } from "./components/Charts";
import { DemoControls } from "./components/DemoControls";
import { FlowStrip } from "./components/FlowStrip";
import { useEdgeGuard, useNow } from "./useEdgeGuard";

const HEARTBEAT_TIMEOUT_MS = 3000; // the edge app sends a status every second

export default function App() {
  const { view, status, statusAt, connection, duplicates, received, send, url } = useEdgeGuard();
  const now = useNow(1000);
  const heartbeatFresh = statusAt != null && now - statusAt < HEARTBEAT_TIMEOUT_MS;
  const edgeRunning = connection === "connected" && heartbeatFresh && status?.state === "running";

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>EdgeGuard <span className="muted">· Machine 1</span></h1>
          <p className="muted small">Predictive maintenance at the edge · broker {url}</p>
        </div>
        {status && <p className="muted small">model {status.model_version}</p>}
      </header>

      <FlowStrip status={status} heartbeatFresh={heartbeatFresh} connection={connection}
                 liveAgeMs={view.liveAgeMs} received={received} duplicates={duplicates} />
      <DriftBanner m={view.latest} />

      <div className="grid-2">
        <StatusCard m={view.latest} threshold={status?.threshold} costRatio={status?.cost_ratio} />
        <ActionCard m={view.latest} />
      </div>

      <Charts points={view.points} threshold={status?.threshold} />

      <div className="grid-2">
        <AlarmHistory alarms={view.alarms} />
        <DemoControls status={status} enabled={edgeRunning} send={send} />
      </div>
    </div>
  );
}
