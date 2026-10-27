"""cell_guard: the bridge from EdgeGuard (MQTT) to the robot cell (ROS 2).

  MQTT in:   edgeguard/machine1/alarm_state  (retained; the edge app's alarm state machine, D-32)
  ROS 2 out: /machine1/alarm     std_msgs/Bool  an alarm is active right now
             /robot_cell/stop    std_msgs/Bool  the interlock: true while an alarm is latched and
                                                not acknowledged (rule in interlock.py)

Threads: paho-mqtt receives in its own thread and only puts messages into a queue. A ROS timer
(5 times a second) takes them out, updates the interlock and publishes both topics. So all the logic
runs in ROS's thread and needs no locks. Publishing on a timer (not only on change) also works as a
heartbeat: robot_sim stops by itself if these messages stop coming (see robot_sim.py).
"""
import json
import os
import queue

import paho.mqtt.client as mqtt
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from edgeguard_ros.interlock import Interlock

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_ALARM_STATE = "edgeguard/machine1/alarm_state"
PUBLISH_PERIOD_S = 0.2


class CellGuard(Node):
    def __init__(self):
        super().__init__("cell_guard")
        self.interlock = Interlock()
        self.inbox: queue.Queue = queue.Queue()  # (topic, payload) from the MQTT thread
        # Publishers: depth 10 = keep up to 10 messages if a subscriber is slow.
        self.pub_alarm = self.create_publisher(Bool, "/machine1/alarm", 10)
        self.pub_stop = self.create_publisher(Bool, "/robot_cell/stop", 10)
        self.create_timer(PUBLISH_PERIOD_S, self.tick)
        self.mqtt = self.connect_mqtt()

    def connect_mqtt(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgeguard-cell-guard")
        client.reconnect_delay_set(min_delay=1, max_delay=5)

        def on_connect(c, userdata, flags, reason_code, properties):
            # Subscribe again on every (re)connect: the broker forgets non-persistent sessions.
            c.subscribe(TOPIC_ALARM_STATE, 1)  # retained: the current state arrives at once
            self.get_logger().info(f"MQTT connected to {MQTT_HOST}:{MQTT_PORT}")

        client.on_connect = on_connect
        client.on_message = lambda c, userdata, m: self.inbox.put((m.topic, m.payload))
        client.connect_async(MQTT_HOST, MQTT_PORT)
        client.loop_start()  # network loop in a background thread
        return client

    def tick(self):
        was_stop = self.interlock.stop
        while not self.inbox.empty():
            topic, payload = self.inbox.get()
            try:
                self.interlock.on_alarm_state(json.loads(payload))
            except (ValueError, KeyError, TypeError) as ex:
                self.get_logger().warning(f"ignored bad message on {topic}: {ex}")
        if self.interlock.stop != was_stop:
            self.get_logger().info("interlock: STOP robot cell" if self.interlock.stop else "interlock: released")
        self.pub_alarm.publish(Bool(data=self.interlock.alarm_now))
        self.pub_stop.publish(Bool(data=self.interlock.stop))


def main():
    rclpy.init()
    node = CellGuard()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.mqtt.loop_stop()
        node.destroy_node()
        rclpy.try_shutdown()
