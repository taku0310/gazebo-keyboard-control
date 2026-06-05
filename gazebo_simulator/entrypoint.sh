#!/usr/bin/env bash
#
# Gazebo (Ignition) Fortress container entrypoint.
#
# Starts, in order:
#   1. Xvfb            - virtual display so the GUI can render headless
#   2. x11vnc + noVNC  - stream that display to a browser on :8080
#   3. ros_ign bridge  - ROS1 <-> Ignition topic translation
#   4. ign gazebo      - the simulator (foreground; keeps the container alive)
#
# Topic bridge (control_logic -> Ignition, sensors -> ROS):
#   /gazebo/cmd_vel  ROS geometry_msgs/Twist  -> IGN ignition.msgs.Twist
#   /odom            IGN  ignition.msgs.Odometry -> ROS nav_msgs/Odometry
#   /imu             IGN  ignition.msgs.IMU      -> ROS sensor_msgs/Imu
#   /clock           IGN  ignition.msgs.Clock    -> ROS rosgraph_msgs/Clock
set -e

WORLD="${WORLD_FILE:-/root/.gazebo/worlds/empty.world}"
DISPLAY_NUM="${DISPLAY:-:1}"
VNC_PORT=5900
WEB_PORT="${WEB_PORT:-8080}"

source /opt/ros/noetic/setup.bash

echo "🖥️  Starting virtual display ${DISPLAY_NUM}..."
Xvfb "${DISPLAY_NUM}" -screen 0 1280x720x24 &
sleep 2

echo "🌐 Starting VNC + noVNC web UI on :${WEB_PORT}..."
x11vnc -display "${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_PORT}" -bg -quiet
# noVNC ships a launcher; fall back to websockify if not present.
if [ -x /usr/share/novnc/utils/launch.sh ]; then
  /usr/share/novnc/utils/launch.sh --vnc "localhost:${VNC_PORT}" --listen "${WEB_PORT}" &
else
  websockify --web=/usr/share/novnc "${WEB_PORT}" "localhost:${VNC_PORT}" &
fi

echo "🔗 Starting ROS1 <-> Ignition bridge..."
rosrun ros_ign_bridge parameter_bridge \
  "/gazebo/cmd_vel@geometry_msgs/Twist]ignition.msgs.Twist" \
  "/odom@nav_msgs/Odometry[ignition.msgs.Odometry" \
  "/imu@sensor_msgs/Imu[ignition.msgs.IMU" \
  "/clock@rosgraph_msgs/Clock[ignition.msgs.Clock" \
  &
BRIDGE_PID=$!

# Stop background jobs cleanly on exit.
cleanup() {
  echo "🧹 Stopping Gazebo container services..."
  kill "${BRIDGE_PID}" 2>/dev/null || true
  pkill -x x11vnc 2>/dev/null || true
  pkill -f websockify 2>/dev/null || true
  pkill -x Xvfb 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "🚀 Launching Ignition Gazebo with world: ${WORLD}"
# -r: run physics immediately; -v 3: info logging. Runs in the foreground so
# the container's lifetime tracks the simulator.
exec ign gazebo -r -v 3 "${WORLD}"
