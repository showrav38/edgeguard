# Decision log

One entry per decision. Format: what we decided, what else we considered, why (3–5 lines).
Draft entries are written by Claude; I review and rewrite them in my own words.

## Template

### D-XX — <short title> (milestone Mx)
- **Decision:**
- **Alternatives:**
- **Why:**

---

### D-01 — Python environment via `uv` and a local `.venv` (M0)
- **Decision:** create a project-local virtualenv with `uv venv --python 3.11`.
- **Alternatives:** system Python (3.14 on this machine), deadsnakes PPA, conda, run everything
  inside Docker only.
- **Why:** the stack is pinned to Python 3.11 and the system Python is 3.14 (onnxruntime /
  skl2onnx wheels may lag). `uv` downloads 3.11 without sudo and installs packages fast.
  Docker images will also use `python:3.11-slim`, so local and container versions match.

### D-02 — Node via nvm locally, dashboard built inside Docker for compose (M0)
- **Decision:** install Linux Node 20+ with nvm for development; the compose stack builds the
  dashboard in a Node image and serves the static files.
- **Alternatives:** Windows `npm` on the WSL filesystem; apt `nodejs` (often outdated).
- **Why:** Windows npm on a WSL path is slow and breaks file watching; nvm needs no sudo.
  Building in Docker means `docker compose up` works on a machine without Node.

### D-03 — Gradient boosting on the 6 raw signals, balanced sample weights (M1)
- **Decision:** sklearn `GradientBoostingClassifier` (200 trees, depth 3) for both models, input =
  the 6 raw PLC signals, `compute_sample_weight("balanced")`.
- **Alternatives:** random forest; small PyTorch MLP; engineered features (power = torque x speed,
  temperature difference) which would likely help HDF/PWF.
- **Why:** strong on small tabular data, no scaling, exports cleanly with skl2onnx. Raw signals
  keep the edge input = what the PLC sends, and make explanations name real signals.
  Balanced weights stop the model from ignoring the 3.4% failure class.

### D-04 — One failure-type label per row by a fixed priority (M1)
- **Decision:** label = first of TWF > HDF > PWF > OSF. All failure rows (also RNF-only and the 9
  failures without a type) stay in the risk model; only the type model drops them.
- **Alternatives:** multi-label model (one binary model per type); drop the 24 multi-type rows.
- **Why:** the priority order is a convention, not physics; it affects only 24 rows. A single
  multi-class model gives one clear action to the technician. RNF is random by definition.

### D-05 — Alarm threshold chosen by expected cost on the validation set (M1)
- **Decision:** threshold = argmin over 0.01..0.99 of 20 x missed + 1 x false alarms on
  validation (costs via env vars). Result 0.22 vs default 0.50 (validation cost 247 vs 322).
- **Alternatives:** 0.5; maximise F1; fixed recall target.
- **Why:** reflects the business trade-off directly. Chosen on validation, reported on the test
  set, so the test numbers are not optimistic. The cost curve is flat near the minimum, so the
  exact value is not fragile; with only 68 validation failures it is still noisy.

### D-06 — Stratified 60/20/20 split; PLC simulator replays the test rows (M1)
- **Decision:** split once with a fixed seed, save data/{train,val,test}.csv.
- **Alternatives:** cross-validation; time-based split.
- **Why:** the dataset has no real time order, so a stratified random split is fair. Replaying
  unseen test rows means the "live" precision/recall in M3 is an honest estimate.

### D-07 — Check the model against the known AI4I generation rules (M1)
- **Decision:** `rules_check.py` recomputes the 4 published rules on the test set and compares
  them with the model's alarms and predicted type; reports a rules-only baseline. Models unchanged.
- **Alternatives:** trust the metrics only; SHAP / partial-dependence plots.
- **Why:** the labels come from known rules, so we can see directly whether the model learned the
  physics. Result: HDF 100% caught, OSF 90%, PWF 80% (misses sit just above 9000 W: trees on raw
  torque and speed approximate a product badly), 84 of 135 false alarms are in the TWF window, where
  failure is random. A rules baseline beats the model on precision. This is an honest limit of the dataset.

### D-08 — OPC UA interface: one consistent row per cycle, "Seq written last" (M2)
- **Decision:** the PLC writes all signals, then `Seq`. The client subscribes only to `Seq`, then
  reads the whole row in ONE read request and checks that `Seq` did not change meanwhile. A gap in
  `Seq` = a missed cycle. String node ids (`ns=2;s=Machine1.AirTempK`) for readability.
- **Alternatives:** subscribe to every signal (values arrive separately, rows can mix two cycles);
  one structured variable (custom DataType, more complex); polling on a timer (misses or doubles rows).
- **Why:** simple and checkable: the M2 check saw 50/50 consistent rows and 0 gaps at 100 ms.
  A real PLC would offer a consistent data block or a structure. No security (NoSecurity) for the
  demo only; a plant would use certificates.

### D-09 — Drift mode = +8 K on both temperatures and +300 rpm (M2)
- **Decision:** `DriftMode` (writable bool) shifts the replayed signals; labels are not changed.
- **Alternatives:** random noise; replay a different product mix; scale torque.
- **Why:** mimics a real change (hot summer shop floor, new speed recipe) and moves air temperature
  far outside the training range (~295–304 K), so M4 has a clear case to detect. The temperature
  difference stays the same, so this is a "the input looks different" test, not a new failure mode.

### D-10 — Edge loop: write back to the PLC first, then MQTT; latency measured inside the PLC (M3)
- **Decision:** per cycle: read row -> ONNX -> explanation -> action -> ONE OPC UA write (LastSeqProcessed
  last) -> MQTT publish. End-to-end latency = PLC writes Seq -> server receives LastSeqProcessed,
  both timestamps taken in the PLC process (asyncua PostWrite callback), logged to a CSV.
- **Alternatives:** publish MQTT first; measure in a third client or with OPC UA timestamps.
- **Why:** the PLC is the time-critical consumer; MQTT/IT may be slow or down (M4). One clock in one
  process gives an exact number with no clock sync. Result: mean 12.1 ms, p95 14.7 ms at a 100 ms cycle
  (most of it is the 10 ms OPC UA publishing interval; inference + explanation is ~1.1 ms).

### D-11 — Explanation by occlusion with the training median (M3)
- **Decision:** for each signal, set it to its training median, recompute risk; effect = drop in risk.
  Report the top 2. One batched ONNX call (6 rows).
- **Alternatives:** SHAP (more correct, extra dependency, harder to explain); deviation from mean x
  feature importance (cheap but not specific to this reading); no explanation.
- **Why:** model-agnostic, easy to explain in one sentence, uses the deployed ONNX model. Limits: one
  signal at a time (misses interactions), median = "typical" not "healthy", effects do not sum to the risk.
- **Finding:** for a real power failure (9.03 kW, seq 70) the top factor was ProcessTempK: the model
  only alarms there if process temp < ~309.7 K. The explanation is faithful to the MODEL, and it exposed a
  spurious split learned from few high-power failures. Same root cause as D-07 (no power feature).

### D-12 — Three machine states and numeric action codes (M3)
- **Decision:** ALARM (risk >= threshold), WARNING (risk >= 0.5 x threshold), OK. ActionCode 0 none,
  1 replace tool, 2 check cooling, 3 check power/drive, 4 reduce load, 5 watch. Text built from live values.
- **Alternatives:** only alarm/no alarm; free text only.
- **Why:** a "watch" band gives early notice without raising the false-alarm count. A PLC/HMI can react
  to an integer code without parsing text; the technician reads the sentence.

### D-13 — Drift = rolling-window mean shift in training standard deviations, with hysteresis (M4)
- **Decision:** for AirTempK, ProcessTempK, SpeedRpm, TorqueNm: |mean of last 50 readings − training
  mean| / training std; score = max. Warning on at >= 1.0, off below 0.75. ToolWearMin not monitored.
- **Alternatives:** PSI on the training deciles; per-reading out-of-range check; multivariate distance
  (Mahalanobis, isolation forest).
- **Why:** one line to explain, uses the stats saved in M1. Calibrated on the unseen test replay (max 0.55,
  so ~2x margin); drift mode is caught after ~11 readings (1.1 s at 100 ms). Tool wear rises naturally and
  worn tools are what the model should alarm on, not drift. Limits: one signal at a time, mean only.

### D-14 — Store-and-forward with a SQLite outbox, one message in flight (M4)
- **Decision:** every telemetry message goes into a SQLite table first; a thread sends the oldest row
  (QoS 1), waits for its PUBACK, then deletes it. Messages stored while offline get `buffered: true`.
- **Alternatives:** paho's own in-memory queue; publish directly and buffer only when offline (two paths,
  ordering races at reconnect); a file per message; a local broker bridge (mosquitto bridge).
- **Why:** survives an edge app restart (paho's queue does not), and one path + one message in flight
  makes the order guaranteed by construction. At-least-once: consumers drop duplicates by `seq`. Size
  limit drops the oldest rows. Result: 20 s outage, 220 buffered, 0 lost, in order, drained in ~1.6 s.

### D-15 — Broker persistence on; the check uses a persistent MQTT session (M4)
- **Decision:** mosquitto `persistence true` on a named volume; the M4 check subscribes with
  `clean_session=False`.
- **Alternatives:** keep persistence off and hope the check reconnects before the edge app drains its buffer.
- **Why:** after a broker restart the edge app and the test subscriber race to reconnect. Without a
  persistent session the check could report "lost" messages the edge app did deliver. It also mirrors a
  real consumer (historian) that must not miss data while it reconnects itself.

### D-16 — Buffer limit: drop the oldest, count the drops persistently, report them (M5)
- **Decision:** `BUFFER_MAX_ROWS` (default 100k, ~5.5 h at 5 Hz). When full, the oldest rows are deleted;
  the dropped count is kept in a SQLite `meta` table and reported in telemetry (`buffer_dropped`), the status
  heartbeat and the dashboard.
- **Alternatives:** no limit (disk fills up, the edge device dies); drop the NEWEST (keeps stale data, loses
  the current state); downsample old rows.
- **Why:** the newest data matters most to a technician; a bounded disk is a hard edge requirement. Dropping
  must never be silent: the M5 check shows every missing seq is accounted for by the dropped count.

### D-17 — Status heartbeat every second, sent directly (not through the outbox) (M5)
- **Decision:** the edge app publishes `edgeguard/machine1/status` (retained, QoS 0) every second with PLC,
  edge and uplink health: OPC UA read+write time, inference time, PUBACK time, backlog, dropped, drift mode, cycle.
- **Alternatives:** derive health from telemetry only; buffer the status like telemetry.
- **Why:** telemetry is history and must not be lost; status is "now" and a stale one is useless, so it skips the
  buffer (QoS 0: not piled up in paho's memory while the broker is down). That is also why the dashboard can
  show the backlog growing during a simulated outage. Dashboard: no heartbeat for 3 s = "no heartbeat".

### D-18 — Demo commands over MQTT, validated in the edge app; the outage is simulated in the edge app (M5)
- **Decision:** `edgeguard/machine1/cmd` with `drift`, `outage`, `speed`; `commands.py` whitelists and range-checks
  each one, rejections are reported in the status. Drift/speed are written to the simulated PLC (`DriftMode`,
  new writable `CycleMs`). The outage makes the store-and-forward sender treat the uplink as down.
- **Alternatives:** stop the broker (the dashboard would lose its connection too); a separate HTTP control API;
  the dashboard writing OPC UA directly.
- **Why:** one protocol for the browser, and the dashboard stays connected so the outage is visible. The buffering
  code path is the same as in a real outage (M4 tests the real one with `docker stop`). The UI labels it
  "simulated". Risk: the broker has no login, so anyone on the network could send commands; `DEMO_COMMANDS=0`
  turns them off, a plant would use broker ACLs.

### D-19 — Dashboard: duplicates dropped by (seq, ts), updates batched every 200 ms (M5)
- **Decision:** a message is a duplicate when its seq was seen with the same `ts` (QoS 1 re-sends are
  byte-identical); same seq with a new ts = the PLC restarted, accepted. Incoming messages are applied to the view
  every 200 ms. Charts: last 100 points, one measure per chart.
- **Alternatives:** keep only "seq > last seq" (freezes after a PLC restart); re-render per message.
- **Why:** at-least-once delivery needs an idempotent consumer. After an outage the buffer drains at ~150 msg/s;
  batching keeps the page smooth. Pure logic lives in `stream.ts` with vitest tests.
