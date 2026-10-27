// Header: app name, machine, connection status, model version, Pause, guided demo, view toggle, theme.
// Every control is always present at the same place; only its label or colour changes.
import { IxButton, IxIconButton, IxToggleButton } from "@siemens/ix-react";
import type { Status } from "../types";
import type { Connection } from "../useEdgeGuard";
import { GLOSSARY, Help, ICON, StateLabel, type Tone } from "./ui";

export type Mode = "operator" | "engineer";

/** One short connection sentence for the header pill. */
export function connectionState(connection: Connection, edgeRunning: boolean, status: Status | null,
                                paused: boolean): { tone: Tone; text: string } {
  if (paused) return { tone: "info", text: "Paused: screen frozen" };
  if (connection === "connecting") return { tone: "idle", text: "Connecting..." };
  if (connection !== "connected") return { tone: "warn", text: "No connection to this screen" };
  if (!edgeRunning) return { tone: "warn", text: "Edge device not responding" };
  const up = status?.uplink;
  if (up && up.state !== "up") return { tone: "warn", text: "IT link down, data stored" };
  if (up && up.buffer_backlog > 10) return { tone: "info", text: "Link back, sending stored data" };
  return { tone: "ok", text: "Live" };
}

export function Header({ conn, model, mode, setMode, paused, onPause, tourOpen, onTour, onTheme }: {
  conn: { tone: Tone; text: string };
  model: string | null;
  mode: Mode;
  setMode: (m: Mode) => void;
  paused: boolean;
  onPause: () => void;
  tourOpen: boolean;
  onTour: () => void;
  onTheme: () => void;
}) {
  return (
    <header className="header" data-box="header">
      <div className="brand">
        <span className="app-name">EdgeGuard</span>
        <span className="machine">Machine 1</span>
      </div>
      <div className="header-status">
        <span className="conn" data-box="conn" title={conn.text}><StateLabel tone={conn.tone}>{conn.text}</StateLabel></span>
        <span className="model num">Model {model ?? "–"}</span>
      </div>
      <div className="header-actions">
        <span className="pause-wrap">
          <IxButton data-box="pause" className="btn-pause" variant="subtle-secondary" icon={paused ? ICON.resume : ICON.pause}
                    onClick={onPause}>{paused ? "Resume" : "Pause"}</IxButton>
          <Help term="Pause" box="help-pause">{GLOSSARY.pause}</Help>
        </span>
        <IxButton data-box="tour" className="btn-tour" variant="subtle-secondary" icon={ICON.tour} aria-label="Guided demo"
                  disabled={tourOpen} onClick={onTour}><span className="btn-label">Guided demo</span></IxButton>
        <div className="view-toggle" role="group" aria-label="View">
          <IxToggleButton data-box="view-operator" variant="subtle-secondary" pressed={mode === "operator"}
                          onClick={() => setMode("operator")}>Operator</IxToggleButton>
          <IxToggleButton data-box="view-engineer" variant="subtle-secondary" pressed={mode === "engineer"}
                          onClick={() => setMode("engineer")}>Engineer</IxToggleButton>
        </div>
        <IxIconButton data-box="theme" icon={ICON.theme} variant="subtle-tertiary" aria-label="Switch light / dark theme"
                      onClick={onTheme} />
      </div>
    </header>
  );
}
