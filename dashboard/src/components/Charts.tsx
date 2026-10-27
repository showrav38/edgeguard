// Live charts of the last 100 readings. One measure per chart (no dual axes); the temperatures share
// one chart because they share a unit. Data lines are neutral and so are the marks: the alarm threshold is a
// labelled dashed line, the tool limit a dotted one, alarm readings hollow rings. Red is kept for the
// status card (an active alarm), so a chart full of past alarms does not shout.
// Fixed height; before the first reading the same box shows "Waiting for machine data...".
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TOOL_LIMIT_MIN } from "../plain";
import type { Point } from "../stream";
import { Card, GLOSSARY, Help, ICON, Waiting } from "./ui";

interface Series {
  key: keyof Point;
  name: string;
  dashed?: boolean; // the 2nd line of a chart is dashed instead of a second colour
}

const AXIS = {
  stroke: "var(--theme-chart-axes)",
  tick: { fill: "var(--theme-color-soft-text)", fontSize: 12 },
  tickLine: false,
};
const TOOLTIP_STYLE = {
  contentStyle: { background: "var(--theme-chart-tooltip-fill)", border: "1px solid var(--theme-chart-tooltip-bdr)",
                  borderRadius: "var(--theme-default-border-radius)", fontSize: 12, color: "var(--theme-color-std-text)" },
  labelStyle: { color: "var(--theme-color-soft-text)" },
};

// Recharts calls this for every point of the first line: a hollow ring on alarm readings, nothing elsewhere.
const alarmDot = (p: any) => p.payload?.alarm
  ? <circle key={p.index} cx={p.cx} cy={p.cy} r={4} fill="var(--theme-color-2)" stroke="var(--theme-color-std-text)" strokeWidth={2} />
  : <g key={p.index} />;

function Chart({ box, title, unit, points, series, domain, threshold, limit, help, xKey = "seq", height }: {
  box: string;
  title: string;
  unit: string;
  points: Point[];
  series: Series[];
  domain?: [number | string, number | string];
  threshold?: number;
  limit?: { y: number; label: string }; // a dashed "usual limit" line (e.g. tool life)
  help?: React.ReactNode;
  xKey?: "seq" | "time"; // the operator view shows clock time, the engineer view the reading number
  height: number;
}) {
  const fmt = (v: number) => (unit === "%" ? `${(v * 100).toFixed(1)} %` : `${v.toFixed(1)} ${unit}`);
  return (
    <Card box={box} title={`${title} (${unit})`} icon={ICON.chart} className="chart-card" help={help}>
      <div className="chart-area" style={{ height }}>
        {points.length === 0 ? <Waiting /> : (
          <ResponsiveContainer width="100%" height={height}>
            <LineChart data={points} margin={{ top: 16, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--theme-chart-grid-lines)" strokeOpacity={0.6} vertical={false} />
              <XAxis dataKey={xKey} {...AXIS} minTickGap={48} />
              <YAxis {...AXIS} width={48} domain={domain ?? ["auto", "auto"]}
                     tickFormatter={(v: number) => (unit === "%" ? `${Math.round(v * 100)}` : `${Math.round(v)}`)} />
              <Tooltip {...TOOLTIP_STYLE} isAnimationActive={false}
                       labelFormatter={(x) => (xKey === "seq" ? `seq ${x}` : `${x}`)}
                       formatter={(v, name) => [fmt(Number(v)), name]} />
              {threshold != null && (
                <ReferenceLine y={threshold} stroke="var(--theme-color-soft-text)" strokeDasharray="6 4"
                               label={{ value: `Alarm ${Math.round(threshold * 100)} %`, position: "insideTopRight",
                                        fill: "var(--theme-color-soft-text)", fontSize: 12 }} />
              )}
              {limit && (
                <ReferenceLine y={limit.y} stroke="var(--theme-color-soft-text)" strokeDasharray="2 3"
                               label={{ value: limit.label, position: "insideTopLeft", fill: "var(--theme-color-soft-text)", fontSize: 12 }} />
              )}
              {series.map((s, i) => (
                <Line key={s.key} dataKey={s.key} name={s.name} stroke="var(--theme-color-std-text)" strokeWidth={1.5}
                      strokeDasharray={s.dashed ? "6 3" : undefined} strokeOpacity={s.dashed ? 0.7 : 1}
                      dot={i === 0 ? alarmDot : false} activeDot={{ r: 3 }} isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
      <p className="small muted chart-key">
        <span className="dot-key" aria-hidden="true" /> alarm reading
        {series.length > 1 && <> · solid: {series[0].name} · dashed: {series[1].name}</>}
        {" · last 100 readings"}
      </p>
    </Card>
  );
}

const one = (key: keyof Point, name: string): Series[] => [{ key, name }];
const TEMPS: Series[] = [
  { key: "AirTempK", name: "air" },
  { key: "ProcessTempK", name: "process", dashed: true },
];

/** Engineer view: every signal, x-axis = reading number (seq). */
export function Charts({ points, threshold }: { points: Point[]; threshold?: number }) {
  return (
    <section className="charts" aria-label="Live signals, last 100 readings">
      <div className="chart-wide">
        <Chart box="chart-eng-risk" title="Failure risk" unit="%" points={points} series={one("risk", "Risk")}
               domain={[0, 1]} threshold={threshold} height={176} />
      </div>
      <Chart box="chart-eng-wear" title="Tool wear" unit="min" points={points} series={one("ToolWearMin", "Tool wear")}
             domain={[0, 260]} limit={{ y: TOOL_LIMIT_MIN, label: "Usual limit 200 min" }} height={144} />
      <Chart box="chart-eng-torque" title="Torque" unit="Nm" points={points} series={one("TorqueNm", "Torque")} height={144} />
      <Chart box="chart-eng-temp" title="Temperatures" unit="K" points={points} series={TEMPS} height={144} />
      <Chart box="chart-eng-speed" title="Rotational speed" unit="rpm" points={points} series={one("SpeedRpm", "Speed")} height={144} />
    </section>
  );
}

/** Operator view: only risk and tool wear. */
export function OperatorCharts({ points, threshold, costRatio }: { points: Point[]; threshold?: number; costRatio?: number }) {
  return (
    <section className="charts-2" aria-label="Live charts, last 100 readings">
      <Chart box="chart-op-risk" title="Failure risk" unit="%" points={points} series={one("risk", "Risk")} xKey="time"
             domain={[0, 1]} threshold={threshold} height={200}
             help={<Help term="Alarm threshold" box="help-threshold-chart">{GLOSSARY.threshold(threshold, costRatio)}</Help>} />
      <Chart box="chart-op-wear" title="Tool wear" unit="min" points={points} series={one("ToolWearMin", "Tool wear")} xKey="time"
             domain={[0, 260]} limit={{ y: TOOL_LIMIT_MIN, label: "Usual limit 200 min" }} height={200}
             help={<Help term="Tool wear" box="help-toolwear-chart">{GLOSSARY.toolWear}</Help>} />
    </section>
  );
}
