// Message shapes, as published by the edge app (edge_app/main.py).

export type MachineState = "OK" | "WARNING" | "ALARM";

export interface Signals {
  AirTempK: number;
  ProcessTempK: number;
  SpeedRpm: number;
  TorqueNm: number;
  ToolWearMin: number;
  ProductType: string;
}

// edgeguard/machine1/telemetry: one message per PLC cycle, sent through the store-and-forward buffer.
export interface Telemetry {
  seq: number;
  ts: string; // ISO time, set by the edge app when the reading was processed
  signals: Signals;
  risk: number;
  alarm: boolean;
  state: MachineState;
  failure_type: string; // tool_wear | heat_dissipation | power | overstrain | none
  action: { code: number; text: string };
  top_factors: { signal: string; effect: number }[];
  drift_score: number | null;
  drift_warning: boolean;
  drift_signals: string[];
  inference_ms: number;
  plc_io_ms: number;
  buffered: boolean; // stored while the uplink was down, delivered late
  buffer_backlog: number;
  buffer_dropped: number;
  ground_truth: { failure: boolean; type: string };
  missed_cycles: number;
  alarm_suppressed?: boolean; // ALARM during the cool-down of its type: recorded, not latched (edge_app/alarms.py)
}

// edgeguard/machine1/alarm_state (retained): the edge app's alarm state machine (edge_app/alarms.py, D-32).
// The ONE source of truth for latch, acknowledge and cool-down: every dashboard and the robot cell follow it.
export type AlarmTypeState = "NORMAL" | "ACTIVE" | "WAITING_FOR_ACK" | "COOLDOWN";

export interface AlarmTypeInfo {
  state: AlarmTypeState;
  acknowledged: boolean;
  start_ts: string | null; // latched alarm: first ALARM reading
  end_ts: string | null; // WAITING_FOR_ACK: when the readings left ALARM
  peak: Telemetry | null; // latched alarm: its highest-risk reading
  cooldown_until: string | null;
  group: { count: number; since: string | null }; // "N alarms in the last X min"
}

export interface AlarmState {
  ts: string;
  latched: boolean;
  acknowledged: boolean; // latched and nothing waits for an Acknowledge (the robot stops while latched && !acknowledged)
  active: boolean;
  types: Record<string, AlarmTypeInfo>;
  counters: { total: number; false: number; last_10min: number; watched_s: number };
  cooldown_s: number;
}

// edgeguard/machine1/status (retained): heartbeat every second, NOT buffered.
export interface Status {
  state: "running" | "offline";
  ts?: string;
  model_version: string;
  threshold: number;
  cost_ratio: number;
  plc?: {
    connected: boolean;
    last_seq: number | null;
    io_ms: number | null;
    missed_cycles: number;
    drift_mode: boolean | null;
    cycle_ms: number | null;
  };
  edge?: { inference_ms: number | null };
  uplink?: {
    state: "up" | "down" | "simulated_down";
    outage_remaining_s: number;
    ack_ms: number | null;
    buffer_backlog: number;
    buffer_dropped: number;
    buffer_max: number;
  };
  last_command?: { cmd?: string; ok: boolean; error?: string } | null;
}

// edgeguard/robot_cell/status (retained): from the ROS 2 node robot_sim (ros2/edgeguard_ros/robot_sim.py).
export interface RobotStatus {
  state: "running" | "stopped" | "offline";
  reason: string; // "running", "stopped by EdgeGuard", "stopped: no interlock signal", "robot_sim not running"
  parts: number | null; // parts loaded since robot_sim started
  ts?: string;
}

// edgeguard/machine1/cmd (see edge_app/commands.py)
export type Command =
  | { cmd: "drift"; on: boolean }
  | { cmd: "outage"; seconds: number }
  | { cmd: "speed"; cycle_ms: number }
  | { cmd: "ack"; type: string | null; ts: string | null }; // Acknowledge: decided by the edge app
