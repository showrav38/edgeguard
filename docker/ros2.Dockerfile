# ROS 2 Jazzy (ros-base: rclpy, std_msgs, launch, ros2 CLI; no GUI tools) + the edgeguard_ros package.
FROM ros:jazzy-ros-base
# paho-mqtt 2.x like the edge app (Ubuntu 24.04 only packages 1.6). The image is Ubuntu's system Python,
# so pip needs --break-system-packages; acceptable inside a single-purpose container.
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
    && pip install --no-cache-dir --break-system-packages "paho-mqtt>=2,<3" \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /ros_ws
COPY ros2/edgeguard_ros src/edgeguard_ros
COPY ros2/healthcheck.sh /healthcheck.sh
# colcon builds the package into /ros_ws/install.
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select edgeguard_ros
ENV PYTHONUNBUFFERED=1
# Source ROS and our workspace, then start both nodes.
CMD ["bash", "-c", "source /ros_ws/install/setup.bash && exec ros2 launch edgeguard_ros edgeguard.launch.py"]
