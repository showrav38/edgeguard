// A state label with an icon shape AND a colour, so the state never relies on colour alone.
import type { ReactNode } from "react";
export type Tone = "ok" | "warn" | "bad" | "idle";

const ICON: Record<Tone, string> = { ok: "●", warn: "▲", bad: "■", idle: "○" };

export function ToneLabel({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`tone tone-${tone}`}>
      <span aria-hidden="true">{ICON[tone]}</span> {children}
    </span>
  );
}
