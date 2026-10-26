// "?" next to a technical term. Hover or keyboard focus shows the explanation; a tap toggles it (phones).
// On small screens the bubble is pinned to the bottom of the screen so it never runs off the edge.
import { useState, type ReactNode } from "react";

export function Help({ term, children }: { term: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span className={`help-wrap${open ? " open" : ""}`}>
      <button type="button" className="help" aria-label={`What does "${term}" mean?`} aria-expanded={open}
              onClick={() => setOpen((o) => !o)} onBlur={() => setOpen(false)}>?</button>
      <span role="tooltip" className="help-pop"><strong>{term}</strong>: {children}</span>
    </span>
  );
}

// The explanations, in one place so both views say the same thing.
export const GLOSSARY = {
  risk: "the model's estimate of how likely the machine is to fail at this reading, from 0 % to 100 %. "
    + "It is an estimate, not a certainty.",
  threshold: (threshold?: number, costRatio?: number) =>
    `the risk level that raises an alarm${threshold != null ? ` (now ${Math.round(threshold * 100)} %)` : ""}. `
    + `It was chosen by cost: a missed failure counts ${costRatio ?? 20}× more than a false alarm, `
    + "so the alarm fires early, at a low risk.",
  drift: "the live readings look different from the data the model learned from (for example a hotter "
    + "shop floor). The model may then be wrong, so check the machine yourself.",
  buffer: "when the connection to plant IT is down, the edge device stores every reading locally and sends "
    + "them in the original order once the connection is back. Nothing is lost.",
  toolWear: "how many minutes the current cutting tool has been in use. It goes back to 0 when the tool is replaced.",
  acknowledge: "confirms that you have seen the alarm. An alarm stays on screen until someone acknowledges it, "
    + "even if the readings return to normal.",
  latency: "how long a step takes. Low and steady is good.",
};
