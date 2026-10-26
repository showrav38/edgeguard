// EdgeGuard dashboard: one machine, two views of the same live data.
//   Operator view (default): traffic light + one sentence, what to do and why, risk gauge,
//     risk and tool-wear charts, alarm history as sentences. Readable in 5 seconds.
//   Engineer view: data-flow strip, latency, buffer, drift numbers, all signal charts, demo controls.
// "Show me how it works" opens a 4-step guided demo on top of the Operator view.
import { useState } from "react";
import { AlarmHistory } from "./components/AlarmHistory";
import { ActionCard, DriftBanner, StatusCard } from "./components/Cards";
import { Charts, OperatorCharts } from "./components/Charts";
import { DemoControls } from "./components/DemoControls";
import { FlowStrip } from "./components/FlowStrip";
import { GuidedDemo } from "./components/GuidedDemo";
import { AlarmStory, OperatorNotices, RiskGauge, StatusHero, WhatToDo } from "./components/Operator";
import { useEdgeGuard, useNow } from "./useEdgeGuard";

const HEARTBEAT_TIMEOUT_MS = 3000; // the edge app sends a status every second

type Mode = "operator" | "engineer";

// The chosen view is remembered per browser (a convenience only; storage may be blocked).
function loadMode(): Mode {
  try {
    return localStorage.getItem("edgeguard.view") === "engineer" ? "engineer" : "operator";
  } catch {
    return "operator";
  }
}

export default function App() {
  const { view, status, statusAt, connection, duplicates, received, send, ack, url } = useEdgeGuard();
  const now = useNow(1000);
  const [mode, setModeState] = useState<Mode>(loadMode);
  const [tour, setTour] = useState(false);
  const heartbeatFresh = statusAt != null && now - statusAt < HEARTBEAT_TIMEOUT_MS;
  const edgeRunning = connection === "connected" && heartbeatFresh && status?.state === "running";

  const setMode = (m: Mode) => {
    setModeState(m);
    try { localStorage.setItem("edgeguard.view", m); } catch { /* storage blocked: keep it in memory */ }
  };
  const startTour = () => { setMode("operator"); setTour(true); };

  return (
    <div className={`page${tour ? " with-tour" : ""}`}>
      <header className="header">
        <div>
          <h1>EdgeGuard <span className="muted">· Machine 1</span></h1>
          <p className="muted small">
            Predictive maintenance at the edge{mode === "engineer" && ` · broker ${url}`}
            {status && ` · model ${status.model_version}`}
          </p>
        </div>
        <div className="header-actions">
          <div className="segmented view-toggle" role="group" aria-label="View">
            <button aria-pressed={mode === "operator"} onClick={() => setMode("operator")}>Operator view</button>
            <button aria-pressed={mode === "engineer"} onClick={() => setMode("engineer")}>Engineer view</button>
          </div>
          {!tour && <button className="primary" onClick={startTour}>Show me how it works</button>}
        </div>
      </header>

      {mode === "operator" ? (
        <>
          <OperatorNotices latest={view.latest} status={status} connection={connection} />
          <StatusHero latest={view.latest} latched={view.latched} live={edgeRunning} now={now} onAck={ack} />
          <div className="op-grid">
            <WhatToDo latest={view.latest} latched={view.latched} live={edgeRunning} />
            <RiskGauge risk={edgeRunning ? view.latest?.risk ?? null : null} threshold={status?.threshold} costRatio={status?.cost_ratio} />
          </div>
          <OperatorCharts points={view.points} threshold={status?.threshold} costRatio={status?.cost_ratio} />
          <AlarmStory alarms={view.alarms} />
        </>
      ) : (
        <>
          <FlowStrip status={status} heartbeatFresh={heartbeatFresh} connection={connection}
                     liveAgeMs={view.liveAgeMs} received={received} duplicates={duplicates} />
          <DriftBanner m={view.latest} />
          <div className="grid-2">
            <StatusCard m={view.latest} latched={view.latched} now={now} onAck={ack}
                        threshold={status?.threshold} costRatio={status?.cost_ratio} />
            <ActionCard latest={view.latest} latched={view.latched} />
          </div>
          <Charts points={view.points} threshold={status?.threshold} />
          <div className="grid-2">
            <AlarmHistory alarms={view.alarms} />
            <DemoControls status={status} enabled={edgeRunning} send={send} />
          </div>
        </>
      )}

      {tour && <GuidedDemo view={view} status={status} enabled={edgeRunning} send={send} onClose={() => setTour(false)} />}
    </div>
  );
}
