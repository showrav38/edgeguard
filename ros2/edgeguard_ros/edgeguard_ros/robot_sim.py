"""robot_sim: a simulated robot that loads parts into Machine 1 (no real robot, no motion planning).

  ROS 2 in:  /robot_cell/stop    std_msgs/Bool    from cell_guard
  ROS 2 out: /robot_cell/state   std_msgs/String  "running" or "stopped by EdgeGuard" (for `ros2 topic echo`)
  MQTT out:  edgeguard/robot_cell/status (retained) {state, reason, parts, ts} for the dashboard

States:
  running                        loads one part every PART_EVERY_S seconds
  stopped by EdgeGuard           /robot_cell/stop is true
  stopped: no interlock signal   no stop message for STOP_TIMEOUT_S (cell_guard or ROS down):
                                 fail-safe, the robot does not run without a live interlock
"""
import json
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_STATUS = "edgeguard/robot_cell/status"
PART_EVERY_S = float(os.getenv("ROBOT_PART_EVERY_S", "2"))
STOP_TIMEOUT_S = 2.0  # cell_guard publishes 5 times a second


class RobotSim(Node):
    def __init__(self):
        super().__init__("robot_sim")
        self.stop_requested = True       # stopped until the first interlock message says otherwise
        self.last_stop_msg = 0.0         # time.monotonic() of the last /robot_cell/stop message
        self.parts = 0
        self.next_part_at = 0.0
        self.state = "stopped: no interlock signal"
        self.create_subscription(Bool, "/robot_cell/stop", self.on_stop, 10)
        self.pub_state = self.create_publisher(String, "/robot_cell/state", 10)
        self.create_timer(0.5, self.tick)
        self.mqtt = self.connect_mqtt()

    def connect_mqtt(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-robot-sim")
        # Last will: if this process dies, the broker publishes "offline" for us.
        client.will_set(TOPIC_STATUS, json.dumps({"state": "offline", "reason": "robot_sim not running", "parts": None}),
                        qos=1, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=5)
        client.connect_async(MQTT_HOST, MQTT_PORT)
        client.loop_start()
        return client

    def on_stop(self, msg: Bool):
        self.stop_requested = msg.data
        self.last_stop_msg = time.monotonic()

    def tick(self):
        now = time.monotonic()
        if now - self.last_stop_msg > STOP_TIMEOUT_S:
            state = "stopped: no interlock signal"
        elif self.stop_requested:
            state = "stopped by EdgeGuard"
        else:
            state = "running"

        if state != self.state:
            self.get_logger().info(f"robot cell: {state} (parts loaded: {self.parts})")
            self.state = state
            self.next_part_at = now + PART_EVERY_S  # a part cycle starts fresh after a stop
        if state == "running" and now >= self.next_part_at:
            self.parts += 1
            self.next_part_at = now + PART_EVERY_S

        self.pub_state.publish(String(data=state))
        status = {
            "state": "running" if state == "running" else "stopped",
            "reason": state,
            "parts": self.parts,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        self.mqtt.publish(TOPIC_STATUS, json.dumps(status), qos=0, retain=True)


def main():
    rclpy.init()
    node = RobotSim()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.mqtt.loop_stop()
        node.destroy_node()
        rclpy.try_shutdown()
