# Starts both nodes: `ros2 launch edgeguard_ros edgeguard.launch.py`. Each node runs in its own process;
# they find each other through ROS 2 discovery (DDS), not through this file.
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package="edgeguard_ros", executable="cell_guard", name="cell_guard", output="screen"),
        Node(package="edgeguard_ros", executable="robot_sim", name="robot_sim", output="screen"),
    ])
