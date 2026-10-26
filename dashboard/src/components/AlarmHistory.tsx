// Every step into ALARM, newest first, next to what really happened (ground truth from the replay).
import { FAILURE_TYPES, pct } from "../format";
import type { AlarmEvent } from "../stream";

export function AlarmHistory({ alarms }: { alarms: AlarmEvent[] }) {
  return (
    <section className="card">
      <h2>Alarm history</h2>
      {alarms.length === 0 ? (
        <p className="muted">No alarms yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Time</th><th>Seq</th><th>Predicted</th><th className="num">Risk</th><th>Actual (replay)</th></tr>
            </thead>
            <tbody>
              {alarms.map((a) => (
                <tr key={`${a.seq}-${a.ts}`}>
                  <td className="num">{new Date(a.ts).toLocaleTimeString([], { hour12: false })}
                    {a.buffered && <span className="tag">buffered</span>}</td>
                  <td className="num">{a.seq}</td>
                  <td>{FAILURE_TYPES[a.failureType] ?? a.failureType}</td>
                  <td className="num">{pct(a.risk)}</td>
                  <td>{a.actualFailure ? `failure (${a.actualType})` : "no failure (false alarm)"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
