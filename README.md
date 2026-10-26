# EdgeGuard — from alarm to action

Predictive maintenance at the edge: live PLC signals over OPC UA → ONNX failure-risk and
failure-type models → an action the technician can act on, with explanation, cost-based alarm
threshold, drift warning and store-and-forward MQTT.

> Status: work in progress (milestones M0–M5 done: models, PLC simulator, edge app, robustness, dashboard).

## Why this project (ME)
_TODO (ME): write in my own words._

## Architecture
_TODO (M6): mermaid diagram._

## Results
Time-based split by UDI (train = past, test = future; see docs/decisions.md D-20). Test set: UDI 8001–10000,
2,000 readings, 39 failures. The earlier random split is shown for comparison only (different test rows).

| Risk model (test set) | Time split, cost threshold 0.16 | Time split, default 0.50 | Old random split, cost threshold 0.22 |
|---|---|---|---|
| ROC-AUC | 0.955 | 0.955 | 0.976 |
| Recall (failures caught) | 0.85 (33/39) | 0.69 (27/39) | 0.88 (60/68) |
| Precision | 0.21 | 0.38 | 0.31 |
| F1 | 0.33 | 0.49 | 0.46 |
| False alarms / missed | 128 / 6 | 44 / 12 | 135 / 8 |

- **Cost-based threshold:** on validation, cost 252 at 0.16 vs 444 at 0.50 (missed failure = 20 × false alarm).
- **Recall by type (time split):** TWF 0.60 (n=10), PWF 1.00 (n=10), OSF 0.95 (n=21); HDF and RNF do not occur in
  the test period (all heat-dissipation failures happen in UDI 2001–8000). RNF is random by definition.
- **Failure-type model:** accuracy 0.89 on 37 test failures (small n).
- **What the technician sees:** in the time-ordered replay, 104 alarm episodes in 2,000 readings, mean 1.6 readings
  long. Most AI4I failures are single-reading events, so the dashboard latches alarms until acknowledged.
- **Live (M3 check, 500 readings):** precision 0.18, recall 0.90 (10 failures); end-to-end PLC -> edge -> PLC
  latency mean ~12 ms, p95 ~17 ms; inference + explanation ~0.6 ms.

## How to run
_TODO (M6)._

### Configuration (environment variables, defaults)
| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `PLC_CYCLE_MS` | `1000` | plc_sim | one replayed test row (UDI order) per second; the check scripts run faster (100 ms) |
| `OPCUA_ENDPOINT` | `opc.tcp://0.0.0.0:4840/edgeguard/` | plc_sim | OPC UA server endpoint |
| `DRIFT_TEMP_K` / `DRIFT_SPEED_RPM` | `8` / `300` | plc_sim | signal shift when `DriftMode` is on |
| `COST_MISSED_FAILURE` / `COST_FALSE_ALARM` | `20` / `1` | training | costs used to choose the alarm threshold |
| `PLC_LATENCY_LOG` | (off) | plc_sim | CSV of end-to-end latency per Seq |
| `OPCUA_URL` | `opc.tcp://localhost:4840/edgeguard/` | edge_app | PLC to connect to |
| `OPCUA_PUBLISH_MS` | `10` | edge_app | OPC UA subscription publishing interval |
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | edge_app | MQTT broker |
| `WARNING_FRACTION` | `0.5` | edge_app | WARNING when risk >= 0.5 x alarm threshold |
| `MODELS_DIR` | `./models` | edge_app | ONNX models + model_info.json |
| `DRIFT_WINDOW` | `50` | edge_app | readings in the rolling drift window |
| `DRIFT_ON` / `DRIFT_OFF` | from `model_info.json` (2.35 / 1.88) | edge_app | drift warning on / off (window-mean shift, in training std); chosen on UDI 1–8000 by `training/calibrate_drift.py` |
| `DRIFT_SIGNALS` | `AirTempK,ProcessTempK,SpeedRpm,TorqueNm` | edge_app | signals watched for drift |
| `BUFFER_PATH` | `./data/outbox.db` | edge_app | SQLite store-and-forward buffer |
| `BUFFER_MAX_ROWS` | `100000` | edge_app | buffer limit (~28 h at 1 reading/s); oldest dropped when full, count reported |
| `MQTT_CLIENT_ID` | `edgeguard-edge` | edge_app | must be unique per broker |
| `STATUS_INTERVAL_S` | `1.0` | edge_app | status heartbeat interval (PLC / edge / uplink health) |
| `DEMO_COMMANDS` | `1` | edge_app | `0` = ignore the demo command topic `edgeguard/machine1/cmd` |
| `VITE_MQTT_URL` | `ws://<page host>:9001` | dashboard | MQTT websocket URL (set at build time) |

## Honest limits
_TODO (M6): simulated PLC, public semi-synthetic dataset (AI4I 2020), not a real industrial
edge device; random failures (RNF) are not predictable by definition._

## What I learned (ME)
_TODO (ME): write in my own words._

## Next steps
- **Engineered features: power, temperature difference, tool wear × torque.** The models use
  only the 6 raw PLC signals. The rules check (`docs/rules_check.png`) shows where that hurts:
  all 3 missed power failures sit just above the 9,000 W limit, and the missed overstrain
  failures sit just above the tool wear × torque limit. A tree model on raw torque and speed
  can only approximate a product (power, strain) with many small steps. Feeding power
  (torque × speed × 2π/60), temperature difference (process − air) and tool wear × torque
  directly should close these gaps. Cost: the edge app must compute 3 extra inputs, and the
  explanations would partly name derived values instead of raw signals. We kept the raw signals
  for simplicity and clear explanations (see docs/decisions.md D-03, D-07).
- _TODO (M6): more next steps._

## Data
UCI "AI4I 2020 Predictive Maintenance Dataset" (CC BY 4.0). Not committed — place it at
`data/ai4i2020.csv`.

**The dataset is semi-synthetic.** The failure labels were generated by simple published rules
(tool wear window 200–240 min, temperature difference + low speed, power range, tool wear ×
torque), not recorded from a real machine. `training/rules_check.py` compares the learned model
with these rules (`docs/rules_check.png`). On this data the rules alone (HDF/PWF/OSF) reach
precision 1.00 (recall 0.72 vs the model's 0.85 on the time-split test set), so the results show
the pipeline works; they do not show how well the model would do on real machine data.
