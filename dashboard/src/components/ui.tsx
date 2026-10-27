// Small building blocks on top of Siemens iX (open source, MIT; classic theme, no brand theme).
// One icon per meaning, used everywhere; colour only for status. Red is kept for an active alarm only,
// the accent colour for the one main action button (Acknowledge).
import { IxIcon, IxIconButton, IxTooltip } from "@siemens/ix-react";
import {
  iconBulb, iconChevronLeft, iconChevronRight, iconClose, iconCloudUpload, iconError, iconGauge, iconHistory,
  iconHourglass, iconInfo, iconLightDark, iconMaintenance, iconMonitor, iconPause, iconPcTower, iconPlay, iconPlc,
  iconQuestion, iconRoboticArm, iconSingleCheck, iconSuccess, iconTrend, iconWarning,
} from "@siemens/ix-icons/icons";
import { useId, type ReactNode } from "react";

export const ICON = {
  ok: iconSuccess,
  warn: iconWarning,
  bad: iconError,
  info: iconInfo,
  idle: iconHourglass, // waiting / unknown
  ack: iconSingleCheck,
  pause: iconPause,
  resume: iconPlay,
  theme: iconLightDark,
  help: iconQuestion,
  tour: iconBulb,
  todo: iconMaintenance,
  gauge: iconGauge,
  chart: iconTrend,
  history: iconHistory,
  back: iconChevronLeft,
  next: iconChevronRight,
  close: iconClose,
  plc: iconPlc,
  edge: iconPcTower,
  uplink: iconCloudUpload,
  screen: iconMonitor,
  robot: iconRoboticArm,
};

export type Tone = "ok" | "warn" | "bad" | "info" | "idle";

/** A state as icon + word. The icon shape differs per state, so it never relies on colour alone. */
export function StateLabel({ tone, children, big = false }: { tone: Tone; children: ReactNode; big?: boolean }) {
  return (
    <span className={`state state-${tone}${big ? " state-big" : ""}`}>
      <IxIcon name={ICON[tone]} size={big ? "32" : "16"} aria-hidden="true" />
      <span className="state-text">{children}</span>
    </span>
  );
}

/** "?" next to a technical term: an iX tooltip on hover, keyboard focus or tap. */
export function Help({ term, box, children }: { term: string; box: string; children: ReactNode }) {
  const id = `help-${useId().replace(/:/g, "")}`;
  return (
    <>
      <IxIconButton id={id} data-box={box} icon={ICON.help} variant="subtle-tertiary" size="16"
                    aria-label={`What does "${term}" mean?`} className="help" />
      <IxTooltip for={`#${id}`} titleContent={term} className="help-pop">{children}</IxTooltip>
    </>
  );
}

/** Every card: a fixed-size iX-styled surface with a title row. `box` names it for the layout test. */
export function Card({ box, title, icon, help, className = "", tone, children }: {
  box: string;
  title: ReactNode;
  icon?: string;
  help?: ReactNode;
  className?: string;
  tone?: Tone; // a coloured left edge for status cards only
  children: ReactNode;
}) {
  return (
    <section className={`card ${className}${tone ? ` card-${tone}` : ""}`} data-box={box}>
      <h2 className="card-title">
        {icon && <IxIcon name={icon} size="16" aria-hidden="true" />}
        <span>{title}</span>
        {help}
      </h2>
      <div className="card-body">{children}</div>
    </section>
  );
}

/** Shown inside a card (same size) before the first reading arrives. */
export function Waiting({ text = "Waiting for machine data..." }: { text?: string }) {
  return (
    <p className="waiting">
      <IxIcon name={ICON.idle} size="16" aria-hidden="true" /> {text}
    </p>
  );
}

// The explanations, in one place so both views say the same thing.
export const GLOSSARY = {
  risk: "the model's estimate of how likely the machine is to fail at this reading, from 0 % to 100 %. "
    + "It is an estimate, not a certainty.",
  threshold: (threshold?: number, costRatio?: number) =>
    `the risk level that raises an alarm${threshold != null ? ` (now ${Math.round(threshold * 100)} %)` : ""}. `
    + `It was chosen by cost: a missed failure counts ${costRatio ?? 20} times more than a false alarm, `
    + "so the alarm fires early, at a low risk.",
  drift: "the live readings look different from the data the model learned from (for example a hotter "
    + "shop floor). The model may then be wrong, so check the machine yourself.",
  buffer: "when the connection to plant IT is down, the edge device stores every reading locally and sends "
    + "them in the original order once the connection is back. Nothing is lost.",
  toolWear: "how many minutes the current cutting tool has been in use. It goes back to 0 when the tool is replaced.",
  acknowledge: "confirms that you have seen the alarm. An alarm stays on screen until someone acknowledges it, "
    + "even if the readings return to normal. For a short cool-down after that, repeats of the same failure type "
    + "are listed in the history and counted, but do not raise the alarm again.",
  alarmRate: (floodPerHour: number) => "alarms per hour over the last 10 minutes. ISA-18.2 (alarm management) "
    + `calls more than 10 alarms in 10 minutes (${floodPerHour} per hour) an alarm flood; a manageable average is `
    + "about 6 per hour. The low cost-based threshold gives far more on this replay.",
  robot: "a simulated robot loads parts into Machine 1. The ROS 2 node cell_guard stops it while an alarm is "
    + "not acknowledged (an interlock: a person must check the machine first); Acknowledge lets it run again. "
    + "The robot also stops if the interlock signal is missing.",
  latency: "how long a step takes. Low and steady is good.",
  pause: "freezes the screen so you can read it. New readings keep arriving in the background; "
    + "Resume shows them all at once.",
};
