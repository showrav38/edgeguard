// Live charts of the last 100 readings. One measure per chart (no dual axes); the temperatures share
// one chart because they share a unit, with a legend because there are two lines.
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

function Chart({ title, unit, points, series, domain, threshold, height = 150 }: {
  title: string;
  unit: string;
  points: Point[];
  series: Series[];
  domain?: [number | string, number | string];
  threshold?: number;
  height?: number;
}) {
  const fmt = (v: number) => (unit === "%" ? `${(v * 100).toFixed(0)} %` : `${v.toFixed(1)} ${unit}`);
  return (
    <div className="card chart-card">
      <h3>{title} <span className="muted">({unit})</span></h3>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={points} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="seq" {...AXIS} minTickGap={40} />
          <YAxis {...AXIS} width={48} domain={domain ?? ["auto", "auto"]}
                 tickFormatter={(v: number) => (unit === "%" ? `${Math.round(v * 100)}` : `${Math.round(v)}`)} />
          <Tooltip {...TOOLTIP_STYLE} labelFormatter={(seq) => `seq ${seq}`}
                   formatter={(v, name) => [fmt(Number(v)), name]} />
          {threshold != null && (
            <ReferenceLine y={threshold} stroke="var(--critical)" strokeDasharray="4 4"
                           label={{ value: "alarm threshold", position: "insideTopRight", fill: "var(--text-secondary)", fontSize: 11 }} />
          )}
          {series.length > 1 && <Legend verticalAlign="top" height={20} iconType="plainline" wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2}
                  dot={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Charts({ points, threshold }: { points: Point[]; threshold?: number }) {
  const one = (key: keyof Point, name: string): Series[] => [{ key, name, color: "var(--series-1)" }];
  return (
    <section className="charts" aria-label="Live signals, last 100 readings">
      <div className="chart-wide">
        <Chart title="Failure risk" unit="%" points={points} series={one("risk", "Risk")}
               domain={[0, 1]} threshold={threshold} height={180} />
      </div>
      <Chart title="Tool wear" unit="min" points={points} series={one("ToolWearMin", "Tool wear")} />
      <Chart title="Torque" unit="Nm" points={points} series={one("TorqueNm", "Torque")} />
      <Chart title="Temperatures" unit="K" points={points} series={[
        { key: "AirTempK", name: "Air", color: "var(--series-1)" },
        { key: "ProcessTempK", name: "Process", color: "var(--series-2)" },
      ]} />
      <Chart title="Rotational speed" unit="rpm" points={points} series={one("SpeedRpm", "Speed")} />
    </section>
  );
}
