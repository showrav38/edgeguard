// EdgeGuard dashboard: one machine, two views of the same live data.
//   Operator view (default): traffic light + one sentence, what to do and why, risk gauge,
//     risk and tool-wear charts, alarm history as sentences. Readable in 5 seconds.
//   Engineer view: data-flow strip, latency, buffer, drift numbers, all signal charts, demo controls.
// "Guided demo" opens a 4-step tour that lies over the "Recent alarms" card (never over the charts).
//
// Stable layout (docs/decisions.md D-27): every card and button has a fixed place and size in both views.
// A state change only changes text and colour inside an element; nothing appears, disappears or moves.
// e2e/layout.e2e.ts checks this in a headless browser.
import { useState } from "react";
import { AlarmHistory } from "./components/AlarmHistory";
import { ActionCard, DataQuality, StatusCard } from "./components/Cards";
import { Charts, OperatorCharts } from "./components/Charts";
import { DemoControls } from "./components/DemoControls";
import { FlowStrip } from "./components/FlowStrip";
import { GuidedDemo } from "./components/GuidedDemo";
import { connectionState, Header, type Mode } from "./components/Header";
import { AlarmStory, RiskGauge, StatusHero, WhatToDo } from "./components/Operator";
import { toggleTheme } from "./theme";
import { useEdgeGuard } from "./useEdgeGuard";

const HEARTBEAT_TIMEOUT_MS = 3000; // the edge app sends a status every second

// The chosen view is remembered per browser (a convenience only; storage may be blocked).
function loadMode(): Mode {
  try {
    return localStorage.getItem("edgeguard.view") === "engineer" ? "engineer" : "operator";
  } catch {
    return "operator";
  }
}

export default function App() {
  const { screen, paused, togglePause, send, ack } = useEdgeGuard();
  const { view, status, statusAt, connection, received, duplicates, now } = screen;
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
    <div className="page">
      <Header conn={connectionState(connection, edgeRunning, status, paused)} model={status?.model_version ?? null}
              mode={mode} setMode={setMode} paused={paused} onPause={togglePause}
              tourOpen={tour} onTour={startTour} onTheme={toggleTheme} />

      <main className="content">
        {mode === "operator" ? (
          <div className="grid op">
            <StatusHero view={view} live={edgeRunning} paused={paused} now={now} onAck={ack} />
            <WhatToDo view={view} live={edgeRunning} />
            <RiskGauge risk={edgeRunning ? view.latest?.risk ?? null : null} threshold={status?.threshold} costRatio={status?.cost_ratio} />
            <OperatorCharts points={view.points} threshold={status?.threshold} costRatio={status?.cost_ratio} />
            <div className="history-slot">
              <AlarmStory alarms={view.alarms} total={view.alarmTotal} now={now} />
              {tour && <GuidedDemo view={view} status={status} enabled={edgeRunning} send={send} onClose={() => setTour(false)} />}
            </div>
          </div>
        ) : (
          <div className="grid eng">
            <FlowStrip status={status} heartbeatFresh={heartbeatFresh} connection={connection}
                       liveAgeMs={view.liveAgeMs} received={received} duplicates={duplicates} />
            <StatusCard view={view} live={edgeRunning} paused={paused} now={now} onAck={ack}
                        threshold={status?.threshold} costRatio={status?.cost_ratio} />
            <ActionCard latest={view.latest} latched={view.latched} />
            <DataQuality m={view.latest} />
            <Charts points={view.points} threshold={status?.threshold} />
            <AlarmHistory view={view} />
            <DemoControls status={status} enabled={edgeRunning} send={send} />
          </div>
        )}
      </main>

    </div>
  );
}
