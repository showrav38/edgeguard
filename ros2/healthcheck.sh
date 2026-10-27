#!/usr/bin/env bash
# Docker healthcheck for the ros2 service. Healthy = robot_sim publishes its state AND it receives the
# interlock from cell_guard (a state other than "no interlock signal"), i.e. both nodes run and talk over ROS 2.
source /opt/ros/jazzy/setup.bash
state=$(timeout 5 ros2 topic echo --once --field data /robot_cell/state std_msgs/msg/String 2>/dev/null | head -1)
echo "robot_cell/state: ${state:-none}"
[ -n "$state" ] && [ "$state" != "stopped: no interlock signal" ]
