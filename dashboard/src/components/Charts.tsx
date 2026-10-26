// Live charts of the last 100 readings. One measure per chart (no dual axes); the temperatures share
// one chart because they share a unit, with a legend because there are two lines.
// Readings in ALARM get a red dot on every chart, so you can see what the signals did during an alarm.
import { useState, type ReactNode } from "react";
import { TOOL_LIMIT_MIN } from "../plain";
import { GLOSSARY, Help } from "./Help";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Point } from "../stream";

interface Series {
  key: keyof Point;
  name: string;
  color: string;
}

const AXIS = { stroke: "var(--axis)", tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false };
const TOOLTIP_STYLE = {
  contentStyle: { background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 },
  labelStyle: { color: "var(--text-secondary)" },
};

// Recharts calls this for every point of the first line: a red dot on alarm readings, nothing elsewhere.
const alarmDot = (p: any) => p.payload?.alarm
  ? <circle key={p.index} cx={p.cx} cy={p.cy} r={4} fill="var(--critical)" stroke="var(--surface-1)" strokeWidth={1.5} />
  : <g key={p.index} />;

function Chart({ title, unit, points, series, domain, threshold, limit, help, xKey = "seq", height = 150 }: {
  title: string;
  unit: string;
  points: Point[];
  series: Series[];
  domain?: [number | string, number | string];
  threshold?: number;
  limit?: { y: number; label: string }; // a dashed "normal limit" line (e.g. tool life)
  help?: ReactNode;
  xKey?: "seq" | "time"; // the operator view shows clock time, the engineer view the reading number
  height?: number;
}) {
  const fmt = (v: number) => (unit === "%" ? `${(v * 100).toFixed(0)} %` : `${v.toFixed(1)} ${unit}`);
  return (
    <div className="card chart-card">
      <h3>{title} <span className="muted">({unit})</span>{help}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={points} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={xKey} {...AXIS} minTickGap={40} />
          <YAxis {...AXIS} width={48} domain={domain ?? ["auto", "auto"]}
                 tickFormatter={(v: number) => (unit === "%" ? `${Math.round(v * 100)}` : `${Math.round(v)}`)} />
          <Tooltip {...TOOLTIP_STYLE} labelFormatter={(x) => (xKey === "seq" ? `seq ${x}` : `${x}`)}
                   formatter={(v, name) => [fmt(Number(v)), name]} />
          {threshold != null && (
            <ReferenceLine y={threshold} stroke="var(--critical)" strokeDasharray="4 4"
                           label={{ value: "alarm threshold", position: "insideTopRight", fill: "var(--text-secondary)", fontSize: 11 }} />
          )}
          {limit && (
            <ReferenceLine y={limit.y} stroke="var(--warning)" strokeDasharray="4 4"
                           label={{ value: limit.label, position: "insideTopLeft", fill: "var(--text-secondary)", fontSize: 11 }} />
          )}
          {series.length > 1 && <Legend verticalAlign="top" height={20} iconType="plainline" wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s, i) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2}
                  dot={i === 0 ? alarmDot : false} activeDot={{ r: 4 }} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const one = (key: keyof Point, name: string): Series[] => [{ key, name, color: "var(--series-1)" }];
const TEMPS: Series[] = [
  { key: "AirTempK", name: "Air", color: "var(--series-1)" },
  { key: "ProcessTempK", name: "Process", color: "var(--series-2)" },
];

/** Engineer view: every signal, x-axis = reading number (seq). */
export function Charts({ points, threshold }: { points: Point[]; threshold?: number }) {
  return (
    <section className="charts" aria-label="Live signals, last 100 readings">
      <div className="chart-wide">
        <Chart title="Failure risk" unit="%" points={points} series={one("risk", "Risk")}
               domain={[0, 1]} threshold={threshold} height={180} />
      </div>
      <Chart title="Tool wear" unit="min" points={points} series={one("ToolWearMin", "Tool wear")} />
      <Chart title="Torque" unit="Nm" points={points} series={one("TorqueNm", "Torque")} />
      <Chart title="Temperatures" unit="K" points={points} series={TEMPS} />
      <Chart title="Rotational speed" unit="rpm" points={points} series={one("SpeedRpm", "Speed")} />
    </section>
  );
}

/** Operator view: only risk and tool wear; the other signals stay folded away until asked for. */
export function OperatorCharts({ points, threshold, costRatio }: { points: Point[]; threshold?: number; costRatio?: number }) {
  const [all, setAll] = useState(false);
  return (
    <section className="op-charts" aria-label="Live charts, last 100 readings">
      <div className="charts-2">
        <Chart title="Failure risk" unit="%" points={points} series={one("risk", "Risk")} xKey="time"
               domain={[0, 1]} threshold={threshold} height={200}
               help={<><Help term="Risk">{GLOSSARY.risk}</Help><Help term="Alarm threshold">{GLOSSARY.threshold(threshold, costRatio)}</Help></>} />
        <Chart title="Tool wear" unit="min" points={points} series={one("ToolWearMin", "Tool wear")} xKey="time"
               domain={[0, 260]} limit={{ y: TOOL_LIMIT_MIN, label: "normal limit" }} height={200}
               help={<Help term="Tool wear">{GLOSSARY.toolWear}</Help>} />
      </div>
      <div className="chart-foot">
        <span className="muted"><span className="dot-key" aria-hidden="true" /> red dot = alarm · last 100 readings</span>
        <button type="button" aria-expanded={all} onClick={() => setAll((a) => !a)}>
          {all ? "Hide other signals" : "Show all signals"}
        </button>
      </div>
      {all && (
        <div className="charts-3">
          <Chart title="Torque" unit="Nm" points={points} series={one("TorqueNm", "Torque")} xKey="time" />
          <Chart title="Temperatures" unit="K" points={points} series={TEMPS} xKey="time" />
          <Chart title="Spindle speed" unit="rpm" points={points} series={one("SpeedRpm", "Speed")} xKey="time" />
        </div>
      )}
    </section>
  );
}
