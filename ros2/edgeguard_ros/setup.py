# A plain Python package; colcon builds and installs it. Each entry point becomes one ROS 2 node executable.
from setuptools import setup

package_name = "edgeguard_ros"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),  # lets ROS find the package
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/edgeguard.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    entry_points={
        "console_scripts": [
            "cell_guard = edgeguard_ros.cell_guard:main",
            "robot_sim = edgeguard_ros.robot_sim:main",
        ],
    },
)
