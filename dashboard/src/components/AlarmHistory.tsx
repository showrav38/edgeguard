// Every step into ALARM, newest first, next to what really happened (ground truth from the replay).
// Above the table: how many alarms, how many false, and the alarm rate against the ISA-18.2 flood limit.
// Fixed-height card: the table scrolls inside, the card never grows. On a phone each row becomes a small
// block (CSS, labels from data-label), so nothing needs sideways scrolling.
import { FAILURE_TYPES, pct } from "../format";
import { alarmRatePerHour, FLOOD_PER_WINDOW, formatDuration, RATE_WINDOW_MS, type View } from "../stream";
import { Card, GLOSSARY, Help, ICON, StateLabel, type Tone } from "./ui";

const FLOOD_PER_HOUR = FLOOD_PER_WINDOW * (3_600_000 / RATE_WINDOW_MS); // 10 in 10 min = 60 per hour

export function AlarmHistory({ view }: { view: View }) {
  const { alarms, alarmTotal, alarmFalse } = view;
  const rate = alarmRatePerHour(view);
  const flood = view.recentAlarmMs.length > FLOOD_PER_WINDOW;
  const tone: Tone = rate == null ? "idle" : flood ? "warn" : "ok";
  const rateText = rate == null ? "Alarm rate: collecting (needs 1 min)"
    : `${Math.round(rate)} alarms per hour${flood ? ": alarm flood" : ""}`;
  return (
    <Card box="alarms-engineer" title="Alarm history" icon={ICON.history} className="history"
          help={<Help term="Alarm rate" box="help-alarm-rate">{GLOSSARY.alarmRate(FLOOD_PER_HOUR)}</Help>}>
      <div className="rate-row">
        <StateLabel tone={tone}>{rateText}</StateLabel>
        <span className="small muted num">
          {alarmTotal} total · {alarmTotal ? pct(alarmFalse / alarmTotal) : "–"} false (replay)
        </span>
      </div>
      {alarms.length === 0 ? (
        <p className="muted">No alarms yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="alarm-table">
            <thead>
              <tr><th>Start</th><th className="num">Duration</th><th className="num">Seq</th><th>Predicted</th>
                <th className="num">Risk</th><th>Latched</th><th>Actual (replay)</th></tr>
            </thead>
            <tbody>
              {alarms.map((a) => (
                <tr key={`${a.seq}-${a.ts}`}>
                  <td data-label="Start" className="num">{new Date(a.ts).toLocaleTimeString([], { hour12: false })}
                    {a.buffered && <span className="tag">buffered</span>}</td>
                  <td data-label="Duration" className="num">{a.endTs ? formatDuration(Date.parse(a.endTs) - Date.parse(a.ts)) : "active"}</td>
                  <td data-label="Seq" className="num">{a.seq}</td>
                  <td data-label="Predicted">{FAILURE_TYPES[a.failureType] ?? a.failureType}</td>
                  <td data-label="Risk" className="num">{pct(a.risk)}</td>
                  <td data-label="Latched">{a.suppressed ? "no, cool-down" : "yes"}</td>
                  <td data-label="Actual">{a.actualFailure ? `failure (${a.actualType})` : "no failure (false alarm)"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
